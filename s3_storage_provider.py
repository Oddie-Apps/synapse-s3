# -*- coding: utf-8 -*-
# Copyright 2018 New Vector Ltd
# Copyright 2021 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# LOCAL FORK: adds cache-on-read so that fetched files are mirrored into
# Synapse's local media_store as they stream out to the client. Without
# this, every R2 read costs a full round-trip even when /data is a PVC,
# because Synapse only writes locally on upload (store_file), never on
# fetch. Marked changes with "# OA-CACHE".

import logging
import os
import tempfile
import threading

from six import string_types

import boto3
import botocore
from botocore.config import Config

from twisted.internet import defer, reactor
from twisted.python.failure import Failure
from twisted.python.threadpool import ThreadPool

from synapse.logging.context import make_deferred_yieldable
from synapse.module_api import ModuleApi, run_in_background
from synapse.rest.media.v1._base import Responder
from synapse.rest.media.v1.storage_provider import StorageProvider

logger = logging.getLogger("synapse.s3")


# The list of valid AWS storage class names
_VALID_STORAGE_CLASSES = (
    "STANDARD",
    "REDUCED_REDUNDANCY",
    "STANDARD_IA",
    "INTELLIGENT_TIERING",
)

# Chunk size to use when reading from s3 connection in bytes
READ_CHUNK_SIZE = 16 * 1024


class S3StorageProviderBackend(StorageProvider):
    """
    Args:
        hs (HomeServer)
        config: The config returned by `parse_config`
    """

    def __init__(self, hs, config):
        self._module_api: ModuleApi = hs.get_module_api()
        self.cache_directory = hs.config.media.media_store_path
        self.bucket = config["bucket"]
        self.prefix = config["prefix"]
        # A dictionary of extra arguments for uploading files.
        # See https://boto3.amazonaws.com/v1/documentation/api/latest/reference/customizations/s3.html#boto3.s3.transfer.S3Transfer.ALLOWED_UPLOAD_ARGS
        # for a list of possible keys.
        self.extra_args = config["extra_args"]
        self.api_kwargs = {}

        if "region_name" in config:
            self.api_kwargs["region_name"] = config["region_name"]

        if "endpoint_url" in config:
            self.api_kwargs["endpoint_url"] = config["endpoint_url"]

        if "access_key_id" in config:
            self.api_kwargs["aws_access_key_id"] = config["access_key_id"]

        if "secret_access_key" in config:
            self.api_kwargs["aws_secret_access_key"] = config["secret_access_key"]

        if "session_token" in config:
            self.api_kwargs["aws_session_token"] = config["session_token"]

        self.api_kwargs["config"] = Config(
            response_checksum_validation=config.get("response_checksum_validation", "when_required"),
            request_checksum_calculation=config.get("request_checksum_calculation", "when_required")
        )

        self._s3_client = None
        self._s3_client_lock = threading.Lock()

        threadpool_size = config.get("threadpool_size", 40)
        self._s3_pool = ThreadPool(name="s3-pool", maxthreads=threadpool_size)
        self._s3_pool.start()

        # Manually stop the thread pool on shutdown. If we don't do this then
        # stopping Synapse takes an extra ~30s as Python waits for the threads
        # to exit.
        reactor.addSystemEventTrigger(
            "during", "shutdown", self._s3_pool.stop,
        )

    def _get_s3_client(self):
        # this method is designed to be thread-safe, so that we can share a
        # single boto3 client across multiple threads.
        #
        # (XXX: is creating a client actually a blocking operation, or could we do
        # this on the main thread, to simplify all this?)

        # first of all, do a fast lock-free check
        s3 = self._s3_client
        if s3:
            return s3

        # no joy, grab the lock and repeat the check
        with self._s3_client_lock:
            s3 = self._s3_client
            if not s3:
                b3_session = boto3.session.Session()
                self._s3_client = s3 = b3_session.client("s3", **self.api_kwargs)
            return s3

    async def store_file(self, path, file_info):
        """See StorageProvider.store_file"""

        return await self._module_api.defer_to_threadpool(
            self._s3_pool,
            self._get_s3_client().upload_file,
            Filename=os.path.join(self.cache_directory, path),
            Bucket=self.bucket,
            Key=self.prefix + path,
            ExtraArgs=self.extra_args,
        )

    async def fetch(self, path, file_info):
        """See StorageProvider.fetch"""
        d = defer.Deferred()

        # OA-CACHE: pass the local cache path so the streaming task can
        # mirror chunks into Synapse's media_store on the way out.
        local_path = os.path.join(self.cache_directory, path)

        # Don't await this directly, as it will resolve only once the streaming
        # download from S3 is concluded. Before that happens, we want to pass
        # execution back to Synapse to stream the file's chunks.
        #
        # We do, however, need to wrap in `run_in_background` to ensure that the
        # coroutine returned by `defer_to_threadpool` is used, and therefore
        # actually run.
        run_in_background(
            self._module_api.defer_to_threadpool,
            self._s3_pool,
            s3_download_task,
            self._get_s3_client(),
            self.bucket,
            self.prefix + path,
            self.extra_args,
            d,
            local_path,  # OA-CACHE
        )

        # DO await on `d`, as it will resolve once a connection to S3 has been
        # opened. We only want to return to Synapse once we can start streaming
        # chunks.
        return await make_deferred_yieldable(d)

    @staticmethod
    def parse_config(config):
        """Called on startup to parse config supplied. This should parse
        the config and raise if there is a problem.

        The returned value is passed into the constructor.

        In this case we return a dict with fields, `bucket`, `prefix` and `storage_class`
        """
        bucket = config["bucket"]
        prefix = config.get("prefix", "")
        storage_class = config.get("storage_class", "STANDARD")

        assert isinstance(bucket, string_types)
        assert storage_class in _VALID_STORAGE_CLASSES

        result = {
            "bucket": bucket,
            "prefix": prefix,
            "extra_args": {"StorageClass": storage_class},
        }

        if "region_name" in config:
            result["region_name"] = str(config["region_name"])

        if "endpoint_url" in config:
            result["endpoint_url"] = config["endpoint_url"]

        if "access_key_id" in config:
            result["access_key_id"] = str(config["access_key_id"])

        if "secret_access_key" in config:
            result["secret_access_key"] = config["secret_access_key"]

        if "session_token" in config:
            result["session_token"] = config["session_token"]

        if "sse_customer_key" in config:
            result["extra_args"]["SSECustomerKey"] = config["sse_customer_key"]
            result["extra_args"]["SSECustomerAlgorithm"] = config.get(
                "sse_customer_algo", "AES256"
            )

        return result


def s3_download_task(s3_client, bucket, key, extra_args, deferred, local_path=None):
    """Attempts to download a file from S3.

    Args:
        s3_client: boto3 s3 client
        bucket (str): The S3 bucket which may have the file
        key (str): The key of the file
        deferred (Deferred[_S3Responder|None]): If file exists
            resolved with an _S3Responder instance, if it doesn't
            exist then resolves with None.
        local_path (str|None): OA-CACHE — if set, mirror the streamed
            bytes into this local file (tmp + atomic rename on success)
            so subsequent fetches hit the local FS instead of S3.

    Returns:
        A deferred which resolves to an _S3Responder if the file exists.
        Otherwise the deferred fails.
    """
    logger.info("Fetching %s from S3", key)

    try:
        if "SSECustomerKey" in extra_args and "SSECustomerAlgorithm" in extra_args:
            resp = s3_client.get_object(
                Bucket=bucket,
                Key=key,
                SSECustomerKey=extra_args["SSECustomerKey"],
                SSECustomerAlgorithm=extra_args["SSECustomerAlgorithm"],
            )
        else:
            resp = s3_client.get_object(Bucket=bucket, Key=key)

    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey",):
            logger.info("Media %s not found in S3", key)
            return

        reactor.callFromThread(deferred.errback, Failure())
        return

    # OA-CACHE: open a tempfile alongside the target path. Tempfile name
    # includes a random suffix so concurrent fetches for the same key do
    # not race on the same path. We rename to local_path only after a
    # clean stream finish, so partial / aborted downloads never poison
    # the cache.
    cache_file = None
    cache_tmp = None
    if local_path and not os.path.exists(local_path):
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            fd, cache_tmp = tempfile.mkstemp(
                prefix=os.path.basename(local_path) + ".",
                suffix=".s3cache",
                dir=os.path.dirname(local_path),
            )
            cache_file = os.fdopen(fd, "wb")
        except Exception:
            logger.warning("S3 cache: failed to open tmp for %s", local_path, exc_info=True)
            cache_file = None
            cache_tmp = None

    producer = _S3Responder()
    reactor.callFromThread(deferred.callback, producer)
    finished = _stream_to_producer(
        reactor, producer, resp["Body"], timeout=90.0, cache_file=cache_file
    )

    if cache_file is not None:
        try:
            cache_file.close()
        except Exception:
            pass
    if cache_tmp is not None:
        if finished and not os.path.exists(local_path):
            try:
                os.rename(cache_tmp, local_path)
            except Exception:
                logger.warning(
                    "S3 cache: rename %s -> %s failed", cache_tmp, local_path, exc_info=True
                )
                _silent_unlink(cache_tmp)
        else:
            _silent_unlink(cache_tmp)


def _silent_unlink(path):
    try:
        os.remove(path)
    except Exception:
        pass


def _stream_to_producer(reactor, producer, body, status=None, timeout=None, cache_file=None):
    """Streams a file like object to the producer.

    Correctly handles producer being paused/resumed/stopped.

    Args:
        reactor
        producer (_S3Responder): Producer object to stream results to
        body (file like): The object to read from
        status (_ProducerStatus|None): Used to track whether we're currently
            paused or not. Used for testing
        timeout (float|None): Timeout in seconds to wait for consume to resume
            after being paused
        cache_file (file|None): OA-CACHE — if set, every chunk is also
            written to this file as it streams to the consumer.

    Returns:
        bool: True if the body was fully consumed (clean EOF), False if
        we stopped early or hit an error. The caller uses this to decide
        whether to keep the local cache file.
    """

    # Set when we should be producing, cleared when we are paused
    wakeup_event = producer.wakeup_event

    # Set if we should stop producing forever
    stop_event = producer.stop_event

    if not status:
        status = _ProducerStatus()

    finished = False
    try:
        while not stop_event.is_set():
            # We wait for the producer to signal that the consumer wants
            # more data (or we should abort)
            if not wakeup_event.is_set():
                status.set_paused(True)
                ret = wakeup_event.wait(timeout)
                if not ret:
                    raise Exception("Timed out waiting to resume")
                status.set_paused(False)

            # Check if we were woken up so that we abort the download
            if stop_event.is_set():
                return False

            chunk = body.read(READ_CHUNK_SIZE)
            if not chunk:
                finished = True
                return True

            # OA-CACHE: write chunk to local cache file too. On any
            # error, drop caching for the rest of the stream rather than
            # failing the whole request.
            if cache_file is not None:
                try:
                    cache_file.write(chunk)
                except Exception:
                    logger.warning("S3 cache: write failed; disabling for this stream", exc_info=True)
                    cache_file = None

            reactor.callFromThread(producer._write, chunk)

    except Exception:
        reactor.callFromThread(producer._error, Failure())
    finally:
        reactor.callFromThread(producer._finish)
        if body:
            body.close()
    return finished


class _S3Responder(Responder):
    """A Responder for S3. Created by _S3DownloadThread
    """

    def __init__(self):
        # Triggered by responder when more data has been requested (or
        # stop_event has been triggered)
        self.wakeup_event = threading.Event()
        # Trigered by responder when we should abort the download.
        self.stop_event = threading.Event()

        # The consumer we're registered to
        self.consumer = None

        # The deferred returned by write_to_consumer, which should resolve when
        # all the data has been written (or there has been a fatal error).
        self.deferred = defer.Deferred()

    def write_to_consumer(self, consumer):
        """See Responder.write_to_consumer
        """
        self.consumer = consumer
        # We are a IPushProducer, so we start producing immediately until we
        # get a pauseProducing or stopProducing
        consumer.registerProducer(self, True)
        self.wakeup_event.set()
        return make_deferred_yieldable(self.deferred)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()
        self.wakeup_event.set()

    def resumeProducing(self):
        """See IPushProducer.resumeProducing
        """
        # The consumer is asking for more data, signal _S3DownloadThread
        self.wakeup_event.set()

    def pauseProducing(self):
        """See IPushProducer.stopProducing
        """
        self.wakeup_event.clear()

    def stopProducing(self):
        """See IPushProducer.stopProducing
        """
        # The consumer wants no more data ever, signal _S3DownloadThread
        self.stop_event.set()
        self.wakeup_event.set()
        if not self.deferred.called:
            self.deferred.errback(Exception("Consumer ask to stop producing"))

    def _write(self, chunk):
        """Writes the chunk of data to consumer. Called by _S3DownloadThread.
        """
        if self.consumer and not self.stop_event.is_set():
            self.consumer.write(chunk)

    def _error(self, failure):
        """Called when a fatal error occured while getting data. Called by
        _S3DownloadThread.
        """
        if self.consumer:
            self.consumer.unregisterProducer()
            self.consumer = None

        if not self.deferred.called:
            self.deferred.errback(failure)

    def _finish(self):
        """Called when there is no more data to write. Called by _S3DownloadThread.
        """
        if self.consumer:
            self.consumer.unregisterProducer()
            self.consumer = None

        if not self.deferred.called:
            self.deferred.callback(None)


class _ProducerStatus(object):
    """Used to track whether the s3 download thread is currently paused
    waiting for consumer to resume. Used for testing.
    """

    def __init__(self):
        self.is_paused = threading.Event()
        self.is_paused.clear()

    def wait_until_paused(self, timeout=None):
        is_paused = self.is_paused.wait(timeout)
        if not is_paused:
            raise Exception("Timed out waiting")

    def set_paused(self, paused):
        if paused:
            self.is_paused.set()
        else:
            self.is_paused.clear()

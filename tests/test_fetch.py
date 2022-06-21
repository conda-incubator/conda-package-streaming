import io
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from zipfile import ZipFile

import pytest
from requests import HTTPError, Session

from conda_package_streaming import fetch_metadata, lazy_wheel
from conda_package_streaming.fetch_metadata import reader_for_conda_url
from conda_package_streaming.lazy_wheel import LazyConda

LIMIT = 16


@pytest.fixture
def package_url(package_server):
    """
    Base url for all test packages.
    """
    host, port = package_server.server.server_address
    return f"http://{host}:{port}/pkgs"


@pytest.fixture
def package_urls(package_server, package_url):
    pkgs_dir = Path(package_server.app.pkgs_dir)
    urls = []
    for i, path in enumerate(pkgs_dir.iterdir()):
        if i > LIMIT:
            break
        if path.name.endswith((".tar.bz2", ".conda")):
            urls.append(f"{package_url}/{path.name}")
    return urls


def test_stream_url(package_urls):
    with pytest.raises(ValueError):
        next(fetch_metadata.stream_meta("https://localhost/notaconda.rar"))

    for url in package_urls:
        with closing(fetch_metadata.stream_meta(url)) as members:
            print("stream_url", url)
            for tar, member in members:
                if member.name == "info/index.json":
                    break
            else:
                pytest.fail("info/index.json not found")


def test_fetch_meta(package_urls):
    for url in package_urls:
        with tempfile.TemporaryDirectory() as destdir:
            fetch_metadata.fetch_meta(url, destdir)


def test_lazy_wheel(package_urls):
    for url in package_urls:
        if url.endswith(".conda"):
            # API works with `.tar.bz2` but only returns LazyConda for `.conda`
            file_id, conda = reader_for_conda_url(url)
            assert file_id == url.rsplit("/")[-1]
            with conda:
                assert isinstance(conda, LazyConda)
                assert conda.mode == "rb"
                assert conda.readable()
                assert not conda.writable()
                assert not conda.closed
                conda.prefetch("not-appearing-in-archive.txt")

                conda._check_zip()  # zip will figurue this out naturally; delete method?
            break
    else:
        raise LookupError("no .tar.bz2 packages found")

    with pytest.raises(HTTPError):
        reader_for_conda_url(package_urls[0] + ".404.conda")

    class Session200(Session):
        def get(self, *args, **kwargs):
            response = super().get(*args, **kwargs)
            response.status_code = 200
            return response

    with pytest.raises(lazy_wheel.HTTPRangeRequestUnsupported):
        LazyConda(package_urls[0], Session200())

    for url in package_urls:
        if url.endswith(".tar.bz2"):
            LazyConda(url, Session())._check_zip()
            break
    else:
        raise LookupError("no .tar.bz2 packages found")


def test_no_file_after_info():
    """
    If info is the last file, LazyConda must fetch (start of info file .. start
    of zip directory) instead of to the next file in the zip.
    """

    class MockBytesIO(io.BytesIO):
        prefetch = LazyConda.prefetch

        @contextmanager
        def _stay(self):
            yield

    zip = MockBytesIO()
    zf = ZipFile(zip, "w")
    zf.writestr("info-test.tar.zst", b"00000000")  # a short file
    zf.close()

    zip.prefetch("test")


@pytest.mark.skip()
def test_obsolete_lazy_wheel_selftest():
    import logging

    import requests

    logging.basicConfig(level=logging.DEBUG)

    session = requests.Session()

    lzoh = lazy_wheel.LazyZipOverHTTP(
        "https://repodata.fly.dev/repo.anaconda.com/pkgs/main/win-32/current_repodata.jlap",
        session,
    )

    lzoh.seek(1024)
    lzoh.read(768)
    lzoh.seek(0)

    # compare against regular fetch
    with open("outfile.txt", "wb+") as out:
        buf = b" "
        while buf:
            buf = lzoh.read(1024 * 10)
            print(list(zip(lzoh._left, lzoh._right)), lzoh._length)
            if not buf:
                break
            out.write(buf)
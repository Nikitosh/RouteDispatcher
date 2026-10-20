"""Read members of a remote ZIP via HTTP range requests.

usage: remote_zip.py URL list
       remote_zip.py URL get MEMBER OUTFILE
"""
import io
import sys
import urllib.request
import zipfile


class HttpRangeFile(io.RawIOBase):
    def __init__(self, url):
        self.url = url
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])
        self.pos = 0
        self.fetched = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        if whence == 0:
            self.pos = off
        elif whence == 1:
            self.pos += off
        else:
            self.pos = self.size + off
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
        self.pos += len(data)
        self.fetched += len(data)
        return data

    def readinto(self, b):
        d = self.read(len(b))
        b[: len(d)] = d
        return len(d)


if __name__ == "__main__":
    url, cmd = sys.argv[1], sys.argv[2]
    f = HttpRangeFile(url)
    z = zipfile.ZipFile(io.BufferedReader(f, buffer_size=8 * 1024 * 1024))
    if cmd == "list":
        for i in z.infolist():
            print(i.filename, i.file_size, i.compress_size, i.compress_type)
    elif cmd == "get":
        member, out = sys.argv[3], sys.argv[4]
        with z.open(member) as src, open(out, "wb") as dst:
            while True:
                chunk = src.read(16 * 1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
    print(f"# fetched {f.fetched/1e6:.1f} MB", file=sys.stderr)

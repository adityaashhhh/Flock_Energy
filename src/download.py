"""Step 0 - download the official REFIT archives and extract only the files we need.

Run:  python src/download.py
- Tries the link given in the brief first; if that host blocks scripted downloads
  (it sits behind a browser check), falls back to the same file on the university's
  direct file server (same file ID).
- Checks every file's size and SHA-256 against config.EXPECTED, so you can be sure
  these are the exact official files.
- Skips anything already present and verified, so re-running is cheap.
"""
import hashlib
import urllib.error
import urllib.request

import py7zr

from config import DATA, RAW_DIR, CLEAN_DIR, URLS, BRIEF_URLS, EXPECTED, RAW_HOUSE, WM_COLUMN


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def verified(path, name) -> bool:
    size, digest = EXPECTED[name]
    return path.exists() and path.stat().st_size == size and sha256(path) == digest


def try_download(url, dest) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    part = dest.with_name(dest.name + ".part")          # renamed only when complete
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"       could not download from {url.split('/')[2]}: {e}")
        part.unlink(missing_ok=True)
        return False
    part.replace(dest)
    return True


def fetch(name: str) -> None:
    dest = DATA / name
    if verified(dest, name):
        print(f"[ok  ] {name} already downloaded (size + SHA-256 match)")
        return
    for url in dict.fromkeys([BRIEF_URLS[name], URLS[name]]):     # brief link first
        print(f"[get ] {name} from {url.split('/')[2]}  (archives are 0.5-0.7 GB)")
        if try_download(url, dest):
            if verified(dest, name):
                print(f"[ok  ] {name}: size + SHA-256 match the official file")
                return
            print(f"       {name}: file from this link does not match the official checksum")
        print("       trying the next link")
    raise SystemExit(f"Could not obtain a verified copy of {name}.")


def extract(archive: str, targets: list[str], out_dir) -> None:
    missing = [t for t in targets if not (out_dir / t).exists()]
    if not missing:
        print(f"[skip] {archive}: files already extracted")
        return
    print(f"[7z  ] {archive} -> {missing}")
    with py7zr.SevenZipFile(DATA / archive) as z:
        z.extract(path=out_dir, targets=missing)


def main() -> None:
    for name in URLS:
        fetch(name)
    extract("REFIT_RAW_081116.7z",
            [f"RAW_House{RAW_HOUSE}_Part1.csv", f"RAW_House{RAW_HOUSE}_Part2.csv"], RAW_DIR)
    # cleaned House 1 is only used for the cross-check in check_clock.py
    extract("CLEAN_REFIT_081116.7z", [f"CLEAN_House{h}.csv" for h in [RAW_HOUSE] + sorted(WM_COLUMN)], CLEAN_DIR)
    print("download/extract done")


if __name__ == "__main__":
    main()

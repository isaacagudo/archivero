"""Integration tests against the real known-file sets (ADR 0007).

Skipped unless the environment provides them, so the normal suite stays offline:
  ARCHIVERO_BLOOM=/path/to/hashlookup-full.bloom   the CIRCL hashlookup Bloom filter
  ARCHIVERO_ONLINE=1                               also query the CIRCL hashlookup API
The 'Known-file sets' workflow runs them on a self-hosted runner that keeps the filter cached.
"""

import hashlib
import os
import time

import pytest

from archivero.known import BloomFilter, OnlineSet

# NSRL entries that CIRCL's own documentation uses as examples (hashlookup-server README).
KNOWN = ["00000903319A8CE18A03DFA22C07C6CA43602061", "00000079FD7AAC9B2F9C988C50750E1F50B27EB5",
         "FFFFFDAC1B1B4C513896C805C2C698D9688BE69F", "FFFFFF4DB8282D002893A9BAF00E9E9D4BA45E65"]
RANDOM = [hashlib.sha1(b"archivero not a known file %d" % i).hexdigest().upper() for i in range(2000)]

BLOOM = os.environ.get("ARCHIVERO_BLOOM")


@pytest.mark.skipif(not BLOOM or not os.path.isfile(BLOOM), reason="ARCHIVERO_BLOOM not set")
def test_real_bloom_filter():
    t0 = time.time()
    bf = BloomFilter(BLOOM)
    assert bf.count > 100_000_000, "CIRCL's filter holds hundreds of millions of hashes"
    assert all(h.encode() in bf for h in KNOWN)
    assert not any(h.lower().encode() in bf for h in KNOWN[:2])  # the set stores upper case
    fp = sum(h.encode() in bf for h in RANDOM)
    assert fp <= max(5, 20 * bf.p * len(RANDOM)), "false positives far above the rate in the header"
    bf.close()
    assert time.time() - t0 < 30  # mmap: nothing is loaded


@pytest.mark.skipif(os.environ.get("ARCHIVERO_ONLINE") != "1", reason="ARCHIVERO_ONLINE not set")
def test_real_online_api():
    found = OnlineSet().lookup(KNOWN[:2] + RANDOM[:3])
    assert set(found) == set(KNOWN[:2])

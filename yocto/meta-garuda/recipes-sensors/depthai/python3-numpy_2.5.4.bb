# Unbuilt marker for the isolated DepthAI bench's NumPy dependency.
SUMMARY = "NumPy 2.5.4 for OAK reference-sensor Python harness"
HOMEPAGE = "https://numpy.org"
LICENSE = "BSD-3-Clause & 0BSD & MIT & Zlib & CC0-1.0"
# Expression from installed wheel metadata; individual bundled notices retained
# in bench/results/2026-11-12-oak-precheck/installed-licenses.json.
LIC_FILES_CHKSUM = "file://LICENSE.txt;md5=TBD-verify-sdist-before-build"
SRC_URI = "https://files.pythonhosted.org/packages/source/n/numpy/numpy-${PV}.tar.gz"
# Skeleton only. No build inheritance; source checksum/build audit pending.

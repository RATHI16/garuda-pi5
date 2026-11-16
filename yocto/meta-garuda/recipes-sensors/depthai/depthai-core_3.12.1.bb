# Unbuilt dependency marker for the OAK reference-sensor bench, not the product camera.
SUMMARY = "DepthAI core 3.12.1 reference sensor SDK"
HOMEPAGE = "https://github.com/luxonis/depthai-core"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=39a6484daa66eaf84c7ee9c45e34db02"
SRC_URI = "git://github.com/luxonis/depthai-core.git;protocol=https;branch=main"
SRCREV = "da14320483864f7cdabada9e70d4d6a2115eff9a"
# Pi bench installs depthai==3.12.1 Python wheel and its isolated NumPy dependency.
# Transitive/firmware review and actual Yocto build integration remain pending.
# Deliberately no build inheritance: skeleton only.

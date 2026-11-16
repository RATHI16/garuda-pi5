# Unbuilt dependency marker, not a shippable recipe. Resolve exact distro versions first.
SUMMARY = "OAK encoded-video RTP relay dependencies"
LICENSE = "LGPL-2.1-or-later"
HOMEPAGE = "https://gstreamer.freedesktop.org/"
SRC_URI = "git://gitlab.freedesktop.org/gstreamer/gstreamer.git;protocol=https;branch=main"
# Core fdsrc, good rtp/udp, bad videoparsersbad. External executables, no static linking.
# Pin SRCREV and LIC_FILES_CHKSUM from approved target source before enabling this recipe.
# Target packages: gstreamer1.0-tools, gstreamer1.0-plugins-good, gstreamer1.0-plugins-bad.
# Receiver decoding dependencies are separate; no software encoder is used on Pi.

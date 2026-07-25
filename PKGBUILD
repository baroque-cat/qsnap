# Maintainer: qsnap contributors
# PKGBUILD for qsnap — QEMU/KVM snapshot and backup orchestration
#
# pkgver MUST be synchronized with the version field in pyproject.toml.
# The version is read dynamically at runtime via importlib.metadata.

pkgname=qsnap
pkgver=0.3.0
pkgrel=1
pkgdesc="QEMU/KVM snapshot and backup orchestration tool for qcow2 images (btrbk-inspired)"
arch=('any')
url="https://github.com/baroque-cat/qsnap"
license=('MIT')
depends=('python>=3.11' 'libnbd' 'libvirt' 'qemu-full')
makedepends=('python-poetry' 'python-installer' 'git')
# Pin to a git tag so makepkg always checks out the exact release commit.
# Using #tag= instead of #commit= avoids the chicken-and-egg problem:
# a commit hash would change every time we commit the PKGBUILD update itself.
# sha256 is SKIP because the PKGBUILD lives inside the source repo and is
# therefore self-referential (changing the checksum changes the source).
source=("${pkgname}-${pkgver}::git+file://${startdir}#tag=v${pkgver}")
sha256sums=('SKIP')

build() {
  cd "${srcdir}/${pkgname}-${pkgver}"
  poetry build --format wheel
}

package() {
  cd "${srcdir}/${pkgname}-${pkgver}"

  # Install the wheel using python-installer (avoids direct_url.json
  # and pyc path references that pip leaves behind).
  python -m installer --destdir="${pkgdir}" dist/qsnap-*.whl

  # Install systemd unit files
  install -Dm644 systemd/qsnap.service "${pkgdir}/usr/lib/systemd/system/qsnap.service"
  install -Dm644 systemd/qsnap.timer "${pkgdir}/usr/lib/systemd/system/qsnap.timer"
  install -Dm644 systemd/qsnap-check.service "${pkgdir}/usr/lib/systemd/system/qsnap-check.service"
  install -Dm644 systemd/qsnap-check.timer "${pkgdir}/usr/lib/systemd/system/qsnap-check.timer"

  # Install config example
  install -Dm644 qsnap.toml.example "${pkgdir}/etc/qsnap/qsnap.toml.example"

  # Create state directory
  install -d -m755 "${pkgdir}/var/lib/qsnap/state"
}

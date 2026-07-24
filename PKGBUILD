# Maintainer: qsnap contributors
# PKGBUILD for qsnap — QEMU/KVM snapshot and backup orchestration
#
# pkgver MUST be synchronized with the version field in pyproject.toml.
# The version is read dynamically at runtime via importlib.metadata.

pkgname=qsnap
pkgver=0.2.1
pkgrel=1
pkgdesc="QEMU/KVM snapshot and backup orchestration tool for qcow2 images (btrbk-inspired)"
arch=('any')
url="https://github.com/baroque-cat/qsnap"
license=('MIT')
depends=('python>=3.11' 'libnbd' 'libvirt' 'qemu-full')
makedepends=('python-poetry' 'python-installer' 'git')
# Pin to a specific commit so makepkg can compute a real sha256 checksum
# (VCS sources without a #commit= or #tag= fragment always yield 'SKIP').
# Update this hash when bumping pkgver after a new release commit.
source=("${pkgname}-${pkgver}::git+file://${startdir}#commit=10a5e373d01650272106383d12948ed7d8e3eb6a")
sha256sums=('36da01b96afd0ab75045241bb3991b93dfe151b005580a6aa0b8b020e57f9f61')

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

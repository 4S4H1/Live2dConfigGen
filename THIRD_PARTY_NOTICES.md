# Third-party notices

L2D 交互图表编辑器 1.0.0 is distributed with the following third-party
components. The application does not use GPL-only Qt modules.

- **Python 3.13.14** — Python Software Foundation License.
- **PySide6 / Qt for Python 6.11.1** — GNU Lesser General Public License
  version 3 (LGPLv3), with individual bundled Qt libraries retaining the
  license indicated by their Qt distribution metadata.
- **Qt 6.11.1 shared libraries** — LGPLv3/GPLv3/commercial tri-license as
  applicable to each library. The packaged application uses the dynamically
  linked LGPL modules supplied by PySide6. Qt source and license information:
  <https://www.qt.io/licensing/open-source-lgpl-obligations>
- **cryptography 48.0.0** — Apache License 2.0 or BSD 3-Clause License.
- **packaging 26.2** — Apache License 2.0 or BSD 2-Clause License.
- **PyInstaller 6.21.0** — GPLv2 with the special exception described by the
  PyInstaller project for distributing bundled applications.
- **NSIS 3.12** — zlib/libpng license.
- **Pillow 12.1.1** — HPND license.

The local release process copies the license files shipped in the locked Python
environment into both onedir applications and the release's `licenses`
directory. Recipients may replace the dynamically linked Qt libraries with
ABI-compatible versions, subject to the licenses above. The editor's JSON
format and the Ed25519-signed LAN update protocol are application formats and
do not change the licenses of bundled components. This notice is informational
and is not legal advice.

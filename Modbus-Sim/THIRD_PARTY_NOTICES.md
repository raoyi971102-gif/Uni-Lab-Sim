# Third-party notices

## com0com 3.0.0.0

The Modbus-Sim Windows installer contains the unmodified official signed x64
com0com installer as an optional component. com0com is licensed under the GNU
General Public License version 2 (GPL-2.0-only).

To keep redistribution complete and reproducible, the installed
`third_party/com0com` directory includes:

- `Setup_com0com_v3.0.0.0_W7_x64_signed.exe` — unmodified signed x64 binary installer;
- `com0com-3.0.0.0-source.zip` — corresponding official source archive;
- `LICENSE.txt` and `README.txt` — files extracted from that source archive;
- `manifest.json` — official download URLs and SHA-256 checksums used by the build.

Official project and archives:

- Project: <https://sourceforge.net/projects/com0com/>
- Signed binaries: <https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/com0com-3.0.0.0-i386-and-x64-signed.zip/download>
- Corresponding source: <https://sourceforge.net/projects/com0com/files/com0com/3.0.0.0/com0com-3.0.0.0.zip/download>

Modbus-Sim itself is a separate work and does not modify or link against
com0com. It launches the separately installed driver management utility when a
user explicitly asks to manage a virtual port pair.

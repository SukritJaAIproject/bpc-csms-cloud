# Third-party software

The CSMS source (`csms_web_ui_flexxfast1.py`) and the Virtual Charger (`mock_cp.py`) are licensed under the
[Apache License 2.0](LICENSE). The Windows programs in the releases (`FlexxfastCSMS.exe`, `VirtualCharger.exe`, built
with PyInstaller) also contain the software below, each under its own license. The full license texts are part of each
project's source distribution at the address given.

## Embedded in the web pages

| Component | License | Source |
|---|---|---|
| Leaflet 1.9.4 (map) | BSD-2-Clause | https://github.com/Leaflet/Leaflet |
| Map data © OpenStreetMap contributors | ODbL 1.0 (data), tiles under the OSM tile usage policy | https://www.openstreetmap.org/copyright |

## Runtime in the Windows programs

| Component | License | Source |
|---|---|---|
| Python 3.12 (interpreter and standard library) | PSF-2.0 | https://www.python.org |
| OpenSSL 3 (`libssl`, `libcrypto`) | Apache-2.0 | https://www.openssl.org |
| Microsoft Visual C++ runtime (`vcruntime140`, `msvcp140`, `ucrtbase`) | Microsoft redistributable licence | https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist |
| libffi | MIT | https://github.com/libffi/libffi |
| expat | MIT | https://libexpat.github.io |
| zlib | Zlib | https://zlib.net |
| bzip2 | bzip2 licence (BSD-style) | https://sourceware.org/bzip2 |
| liblzma (XZ Utils) | 0BSD / public domain | https://tukaani.org/xz |
| Zstandard (`zstd`) | BSD-3-Clause | https://github.com/facebook/zstd |
| PyInstaller bootloader | GPL-2.0 with the PyInstaller bootloader exception (allows any licence for the bundled program) | https://github.com/pyinstaller/pyinstaller |

## Python packages in the Windows programs

| Package | License | Source |
|---|---|---|
| aiohttp, aiosignal, frozenlist, multidict, yarl | Apache-2.0 | https://github.com/aio-libs |
| aiohappyeyeballs | PSF-2.0 | https://github.com/aio-libs/aiohappyeyeballs |
| anyio | MIT | https://github.com/agronholm/anyio |
| attrs | MIT | https://github.com/python-attrs/attrs |
| Brotli | MIT | https://github.com/google/brotli |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| cffi | MIT | https://github.com/python-cffi/cffi |
| chardet | LGPL-2.1-or-later | https://github.com/chardet/chardet |
| charset-normalizer | MIT | https://github.com/jawah/charset_normalizer |
| colorama | BSD-3-Clause | https://github.com/tartley/colorama |
| cryptography | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| defusedxml | PSF-2.0 | https://github.com/tiran/defusedxml |
| dnspython | ISC | https://github.com/rthalley/dnspython |
| et_xmlfile | MIT | https://foss.heptapod.net/openpyxl/et_xmlfile |
| idna | BSD-3-Clause | https://github.com/kjd/idna |
| importlib-metadata | Apache-2.0 | https://github.com/python/importlib_metadata |
| jsonpointer | BSD-3-Clause | https://github.com/stefankoegl/python-json-pointer |
| jsonschema, jsonschema-specifications, referencing | MIT | https://github.com/python-jsonschema |
| MarkupSafe | BSD-3-Clause | https://github.com/pallets/markupsafe |
| ocpp (Mobility House) | MIT | https://github.com/mobilityhouse/ocpp |
| openpyxl | MIT | https://foss.heptapod.net/openpyxl/openpyxl |
| pyasn1, pyasn1-modules | BSD-2-Clause | https://github.com/pyasn1 |
| pymongo (with bson, gridfs) | Apache-2.0 | https://github.com/mongodb/mongo-python-driver |
| pyOpenSSL | Apache-2.0 | https://github.com/pyca/pyopenssl |
| PySocks | BSD-3-Clause | https://github.com/Anorov/PySocks |
| pywin32 (`pythoncom`, `pywintypes`) | PSF-2.0 | https://github.com/mhammond/pywin32 |
| requests | Apache-2.0 | https://github.com/psf/requests |
| rfc3339-validator, rfc3986-validator | MIT | https://github.com/naimetti/rfc3339-validator |
| rpds-py | MIT | https://github.com/crate-py/rpds |
| service-identity | MIT | https://github.com/pyca/service-identity |
| simplejson | MIT OR AFL-2.1 | https://github.com/simplejson/simplejson |
| six | MIT | https://github.com/benjaminp/six |
| sniffio | MIT OR Apache-2.0 | https://github.com/python-trio/sniffio |
| typing-extensions | PSF-2.0 | https://github.com/python/typing_extensions |
| urllib3 | MIT | https://github.com/urllib3/urllib3 |
| watchdog | Apache-2.0 | https://github.com/gorakhargosh/watchdog |
| websockets | BSD-3-Clause | https://github.com/python-websockets/websockets |
| Werkzeug | BSD-3-Clause | https://github.com/pallets/werkzeug |
| win-inet-pton | Public domain | https://github.com/hickeroar/win_inet_pton |
| zipp | MIT | https://github.com/jaraco/zipp |
| zstandard | BSD-3-Clause | https://github.com/indygreg/python-zstandard |

chardet is used unchanged under the LGPL; its source is available at the address above. The CSMS also runs from its
source in this repository (see "Run locally" in the README), so it can be used with another copy of chardet.

The name and logo of Bhutan Power Corporation Limited are not licensed; see [NOTICE](NOTICE).

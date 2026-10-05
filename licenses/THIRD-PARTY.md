# Third-party components

Monaco Editor 0.52.2 is bundled from the official npm archive, with SHA-512 integrity/provenance in
static/vendor/monaco/provenance.json. Monaco's MIT license and third-party notices are included here.
The vendor regeneration tool verifies the npm package integrity before extracting only regular assets.

The build collects installed Python distributions' license texts and metadata under licenses/python.
PySide6/Qt uses its applicable open-source LGPL/GPL terms; review the supplied LGPL license and Qt notices.
The onedir distribution dynamically loads Qt/PySide libraries in `_internal/PySide6`; compatible replacement
libraries may be substituted there. There is no separate application-level prohibition on replacement or
debugging modifications of those libraries. Corresponding upstream sources are available from
[Qt downloads](https://download.qt.io/official_releases/QtForPython/) and [Qt source repositories](https://code.qt.io/).
The build configuration and application source are in the DistributedExec repository.

The runtime uses the official Python Docker image; its upstream Python/Debian licenses remain in the image.
No new license for the original DistributedExec author's project is chosen by this upgrade.

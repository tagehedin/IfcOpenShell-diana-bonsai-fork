diana-bonsai-fork 
============

This fork should be viewed as an experimental feature request version of the official latest alpha of the BonsaiBIM plugin for Blender. It is an experiment in using Claude Code to add tools and test possible improvements by a user of the software instead of a programmer. With experience in using BIM software in real projects and the possibility to stress-test BonsaiBIM on big files the hope is to contribute in a meaningful way to an exciting project.

BonsaiBIM and Blender
-
BonsaiBIM have the advantage of building on top of Blender that is lightweight, snappy and powerful. Blender is outright fun to use and this is where software like Revit fails miserably. As an Architect you want to work with a tool that inspires and is fun to use and Blender has a good base for visual presentation and realtime rendering.

Critical Problems
-

Big IFC files
-
BonsaiBIM does not work well with big files. The performance of the python based snap system depends directly on the size of the project and is unusable on real-world projects.

Printing
-
BonsaiBIM does not seem to apply proper culling and printing bigger projects and especially tessellated MEP files can provoke exponential print times.

Saving and loading
-
BonsaiBIM loads and saves files in chunks and apply mapping to ifc objects exported from other software and save times can be unreasonably long. Saving in chunks in network environments can trigger anti-virus procedures that make saving times even longer.

General lack of features
-
BonsaiBIM has a very good base and if the big hurdles can be addressed then there is only a question of adding features.

What this fork aims to do
==
diana-bonsai-fork is an experiment in using Claude Code as well as trying to stress-test BonsaiBIM and see if it is possible to find fixes to crucial problems. It is not an attempt at creating clean code but to find out if there are potential solutions to problems.




Test system
-
diana-bonsai-fork is developed on a HP Zbook laptop from 2014, it was used in real world Revit projects until recently but was discarded since it was considered slow. The fact is that Revit projects have not evolved in complexity in the last 12 years and even if computers today are faster today this laptop was actually used in heavy BIM projects and should be able to work still. It is a good test platform since features should be snappy even on older computers.
- System specs:
- Bazzite 44 - Fedora Atomic ublue, 6.19.14-ogc5.1.fc44.x86_64 - Wayland
- CPU: 4 × Intel® Core™ i7-4510U CPU @ 2.00GHz
- Memory: 16 GB.
- Onboard graphics: Mesa Intel® HD Graphics 4400
- Dedicated graphics: AMD Radeon HD 8500M / 8700M
- Hewlett-Packard HP ZBook 14

diana-bonsai-fork features
===
Snap System 2
-
The snap system is using the built in Blender Raycast function instead of the snap system built in python. The advantage is that snapping works on big files.

Printing
-
diana-bonsai-fork is trying to implement culling and other improvements to make printing usable.


Installation and updates
-
1. In Blender, go to **Edit > Preferences > Get Extensions**.
2. Click the dropdown arrow next to "Repositories" (top right) and choose **Add Remote Repository**.
3. Enter this URL:

   ```
   https://tagehedin.github.io/IfcOpenShell-diana-bonsai-fork/index.json
   ```

4. Enable "Check for Updates on Startup" if you want new releases picked up automatically.
5. Find "Bonsai" in the extensions list and install it.

This single URL works regardless of operating system or Blender's bundled Python
version — Blender automatically picks the right download for your platform.

Releases: https://github.com/tagehedin/IfcOpenShell-diana-bonsai-fork/releases

### Troubleshooting: "ModuleNotFoundError: No module named 'ifcopenshell'" after updating on Windows

On Windows, updating an extension in-place can fail to fully replace the
`ifcopenshell` wheel, because Blender can't overwrite `.pyd`/`.dll` files that
are still loaded by the running process. This can leave the extension's
Python environment without `ifcopenshell` installed, causing errors like
`ModuleNotFoundError: No module named 'ifcopenshell'` and
`Couldn't find ifcopenshell wrapper binary` on next startup.

If this happens:

1. In Blender, go to **Edit > Preferences > Get Extensions** and remove/uninstall Bonsai.
2. Close Blender completely.
3. Delete these folders if they exist (under `%APPDATA%\Blender Foundation\Blender\<version>\extensions\`):
   - `.local`
   - `.local_temp`
   - `tagehedin_github_io\bonsai`
4. Restart Blender and install Bonsai again as a fresh install (not an update).



::::::::::::


IfcOpenShell 
============

<p align="center">
<img src="https://github.com/IfcOpenShell/IfcOpenShell/assets/88302/34901387-e2dd-4a0c-8e38-9ffc32a66cde">
</p>


IfcOpenShell is an open source ([LGPL]) software library for working with Industry Foundation Classes ([IFC]). Complete
parsing support is provided for [IFC2x3 TC1], [IFC4 Add2 TC1], IFC4x1, IFC4x2, and [IFC4x3 Add2]. Extensive geometric support
is implemented for the IFC releases [IFC2x3 TC1] and [IFC4 Add2 TC1]. Extending with support for arbitrary IFC schemas
is possible at compile-time when using C++ and at run-time when using Python.

In addition to a C++ and Python API, IfcOpenShell comes with an ecosystem of tools, notably including IfcConvert (an application
to convert IFC models to other formats), Bonsai (an add-on to Blender providing a graphical IFC authoring platform),
and many other libraries, CLI apps, and more. Support is also provided for auxiliary standards such as BCF and IDS.

For more information, see:

* [IfcOpenShell Website](http://ifcopenshell.org)
* [IfcOpenShell Documentation](https://docs.ifcopenshell.org)
  * [IfcOpenShell C++ Installation](https://docs.ifcopenshell.org/ifcopenshell/installation.html)
  * [IfcOpenShell Python Installation](https://docs.ifcopenshell.org/ifcopenshell-python/installation.html)
  * [IfcOpenShell Python Hello World Tutorial](https://docs.ifcopenshell.org/ifcopenshell-python/hello_world.html)
* [Bonsai Website](https://bonsaibim.org)
* [Bonsai Documentation](https://docs.bonsaibim.org/index.html)
  * [Add-on Installation](https://docs.bonsaibim.org/quickstart/installation.html)
  * [Exploring an IFC model](https://docs.bonsaibim.org/quickstart/explore_model.html)
 
Development is sponsored through your generous donations!

[![Open Collective Contributors](https://img.shields.io/opencollective/all/opensourcebim?label=Sponsors&color=22ce5f)](https://opencollective.com/opensourcebim/)

Contents
--------

| Name                      | Description                                                           | License             | Service |
| ------------------------- | --------------------------------------------------------------------- | ------------------- | ------- |
| [bcf](https://docs.ifcopenshell.org/bcf.html)                       | Library to read and write BCF-XML and query OpenCDE BCF-API modules   | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/bcf-client?label=PyPI&color=006dad)](https://pypi.org/project/bcf-client/) [![Anaconda-Server Badge](https://anaconda.org/conda-forge/bcf-client/badges/version.svg)](https://anaconda.org/conda-forge/bcf-client) |
| [bonsai](https://docs.ifcopenshell.org/bonsai.html)                    | Add-on to Blender providing a graphical native IFC authoring platform | GPL-3.0-or-later    | [![Official](https://img.shields.io/badge/BonsaiBIM.org-Download-70ba35)](https://bonsaibim.org/download.html) [![GitHub Unstable](https://img.shields.io/github/v/release/ifcopenshell/ifcopenshell?filter=bonsai-*&label=GitHub-Unstable&color=f6f8fa)](https://github.com/IfcOpenShell/IfcOpenShell/releases?q=bonsai&expanded=true) [![Chocolatey](https://img.shields.io/chocolatey/v/blenderbim-nightly?label=Chocolatey&color=5c9fd8)](https://community.chocolatey.org/packages/blenderbim-nightly/) |
| [bsdd](https://docs.ifcopenshell.org/bsdd.html)                      | Library to query the bSDD API                                         | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/bsdd?label=PyPI&color=006dad)](https://pypi.org/project/bsdd/) |
| [ifc2ca](https://docs.ifcopenshell.org/ifc2ca.html)                    | Utility to convert IFC structural analysis models to Code_Aster       | LGPL-3.0-or-later   |
| [ifc4d](https://docs.ifcopenshell.org/ifc4d.html)                     | Convert to and from IFC and project management software               | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifc4d?label=PyPI&color=006dad)](https://pypi.org/project/ifc4d/) |
| [ifc5d](https://docs.ifcopenshell.org/ifc5d.html)                     | Report and optimise cost information from IFC                         | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifc5d?label=PyPI&color=006dad)](https://pypi.org/project/ifc5d/) |
| [ifcbimtester](https://docs.ifcopenshell.org/bimtester.html)              | Wrapper for Gherkin based unit testing for IFC models                 | LGPL-3.0-or-later   |
| ifcblender                | Historic Blender IFC import add-on                                    | LGPL-3.0-or-later\* |
| [ifccityjson](https://docs.ifcopenshell.org/ifccityjson.html)               | Convert CityJSON to IFC                                               | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifccityjson?label=PyPI&color=006dad)](https://pypi.org/project/ifccityjson/) |
| [ifcclash](https://docs.ifcopenshell.org/ifcclash.html)                  | Clash detection library and CLI app                                   | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcclash?label=PyPI&color=006dad)](https://pypi.org/project/ifcclash/) |
| [ifcconvert](https://docs.ifcopenshell.org/ifcconvert.html)                | CLI app to convert IFC to many other formats                          | LGPL-3.0-or-later\* | [![Official](https://img.shields.io/badge/IfcOpenShell.org-Download-70ba35)](https://docs.ifcopenshell.org/ifcconvert/installation.html) [![GitHub](https://img.shields.io/github/v/release/ifcopenshell/ifcopenshell?filter=ifcconvert-*&label=GitHub&color=f6f8fa)](https://github.com/IfcOpenShell/IfcOpenShell/releases?q=ifcconvert&expanded=true)
| [ifccsv](https://docs.ifcopenshell.org/ifccsv.html)                    | Library and CLI app to export and import schedules from IFC           | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifccsv?label=PyPI&color=006dad)](https://pypi.org/project/ifccsv/) |
| [ifcdiff](https://docs.ifcopenshell.org/ifcdiff.html)                   | Compare changes between IFC models                                    | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcdiff?label=PyPI&color=006dad)](https://pypi.org/project/ifcdiff/) |
| [ifcedit](https://docs.ifcopenshell.org/ifcedit.html)                   | CLI wrapper for ifcopenshell.api IFC model mutation functions         | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcedit?label=PyPI&color=006dad)](https://pypi.org/project/ifcedit/) |
| [ifcfm](https://docs.ifcopenshell.org/ifcfm.html)                     | Extract IFC data for FM handover requirements                         | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcfm?label=PyPI&color=006dad)](https://pypi.org/project/ifcfm/) |
| [ifcmax](https://docs.ifcopenshell.org/ifcmax.html)                    | Historic extension for IFC support in 3DS Max                         | LGPL-3.0-or-later\* | [![Official](https://img.shields.io/badge/IfcOpenShell.org-Download-70ba35)](https://docs.ifcopenshell.org/ifcmax.html)
| [ifcmcp](https://docs.ifcopenshell.org/ifcmcp.html)                    | MCP server for querying and editing IFC building models               | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcopenshell-mcp?label=PyPI&color=006dad)](https://pypi.org/project/ifcopenshell-mcp/) |
| [ifcopenshell-python](https://docs.ifcopenshell.org/ifcopenshell-python.html)       | Python library for IFC manipulation                                   | LGPL-3.0-or-later\* | [![Official](https://img.shields.io/badge/IfcOpenShell.org-Download-70ba35)](https://docs.ifcopenshell.org/ifcopenshell-python/installation.html) [![GitHub](https://img.shields.io/github/v/release/ifcopenshell/ifcopenshell?filter=ifcopenshell-python-*&label=GitHub&color=f6f8fa)](https://github.com/IfcOpenShell/IfcOpenShell/releases?q=ifcopenshell-python&expanded=true) [![PyPI](https://img.shields.io/pypi/v/ifcopenshell?label=PyPI&color=006dad)](https://pypi.org/project/ifcopenshell/) [![Anaconda](https://img.shields.io/conda/vn/conda-forge/ifcopenshell?label=Anaconda&color=43b02a)](https://anaconda.org/conda-forge/ifcopenshell) [![Anaconda](https://img.shields.io/conda/vn/ifcopenshell/ifcopenshell?label=Anaconda-Unstable&color=43b02a)](https://anaconda.org/ifcopenshell/ifcopenshell) [![Docker](https://img.shields.io/docker/pulls/aecgeeks/ifcopenshell?label=Docker&color=1D63ED)](https://hub.docker.com/r/aecgeeks/ifcopenshell) [![AUR](https://img.shields.io/aur/version/ifcopenshell?label=AUR&color=1793d1)](https://aur.archlinux.org/packages/ifcopenshell) [![AUR Unstable](https://img.shields.io/aur/version/ifcopenshell-git?label=AUR-Unstable&color=1793d1)](https://aur.archlinux.org/packages/ifcopenshell-git) [![Pyodide WASM Wheels tag](https://img.shields.io/github/v/tag/ifcopenshell/wasm-wheels?sort=semver&label=pyodide-wasm-wheels)](https://github.com/IfcOpenShell/wasm-wheels) |
| [ifcpatch](https://docs.ifcopenshell.org/ifcpatch.html)                  | Utility to run pre-packaged scripts to manipulate IFCs                | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcpatch?label=PyPI&color=006dad)](https://pypi.org/project/ifcpatch/) |
| [ifcquery](https://docs.ifcopenshell.org/ifcquery.html)                  | CLI tool for querying and inspecting IFC building models              | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifcquery?label=PyPI&color=006dad)](https://pypi.org/project/ifcquery/) |
| [ifcsverchok](https://docs.ifcopenshell.org/ifcsverchok.html)               | Blender Add-on for visual node programming with IFC                   | GPL-3.0-or-later    | [![GitHub](https://img.shields.io/github/v/release/ifcopenshell/ifcopenshell?filter=ifcsverchok-*.*.*&label=GitHub&color=f6f8fa)](https://github.com/IfcOpenShell/IfcOpenShell/releases?q=ifcsverchok&expanded=true)
| [ifctester](https://docs.ifcopenshell.org/ifctester.html)                 | Library, CLI and webapp for IDS model auditing                        | LGPL-3.0-or-later   | [![PyPI](https://img.shields.io/pypi/v/ifctester?label=PyPI&color=006dad)](https://pypi.org/project/ifctester/) |

The IfcOpenShell C++ codebase is split into multiple interal libraries:

| Name                      | Description                                                           | License             |
| ------------------------- | --------------------------------------------------------------------- | ------------------- |
| ifcgeom                   | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| ifcgeom\_schema\_agnostic | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| ifcgeomserver             | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| ifcjni                    | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| ifcparse                  | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| ifcwrap                   | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| qtviewer                  | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |
| serializers               | Internal library for IfcOpenShell                                     | LGPL-3.0-or-later\* |

[LGPL]: https://github.com/IfcOpenShell/IfcOpenShell/tree/master/COPYING.LESSER "LGPL-3.0-or-later"
[IFC]: https://technical.buildingsmart.org/standards/ifc/ "IFC"
[IFC2x3 TC1]: https://standards.buildingsmart.org/IFC/RELEASE/IFC2x3/TC1/HTML/ "IFC2x3 TC1"
[IFC4 Add2 TC1]: https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/ "IFC4 Add2 TC1"
[IFC4x3 Add2]: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/ "IFC4x3 Add2"
[Visual Studio]: https://www.visualstudio.com/ "Visual Studio"
[Visual C++ Build Tools]: http://landinghub.visualstudio.com/visual-cpp-build-tools "Visual C++ Build Tools"
[MSYS2]: https://msys2.github.io/ "MSYS2"
[win/readme.md]: https://github.com/IfcOpenShell/IfcOpenShell/tree/master/win/readme.md "win/readme.md"
[nix/build-all.py]: https://github.com/IfcOpenShell/IfcOpenShell/tree/master/nix/build-all.py "nix/build-all.py"

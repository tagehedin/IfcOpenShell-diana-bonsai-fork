# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2020, 2021 Dion Moult <dion@thinkmoult.com>
#
# This file is part of Bonsai.
#
# Bonsai is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Bonsai is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Bonsai.  If not, see <http://www.gnu.org/licenses/>.

# This file was generated with the assistance of an AI coding tool.
from typing import TYPE_CHECKING

from bpy.props import StringProperty
from bpy.types import PropertyGroup


class BIMLocalizeProperties(PropertyGroup):
    # Strings, not floats: Blender floats are single precision (~0.5 m at 6 600 km northings).
    crs_name: StringProperty(
        name="Coordinate System",
        description="Projected coordinate system of the survey coordinates, e.g. EPSG:3011 (SWEREF 99 18 00)",
    )
    # In project length units, like the Blender Coordinates panel above.
    easting: StringProperty(name="Easting", description="Survey easting that becomes the new local 0,0")
    northing: StringProperty(name="Northing", description="Survey northing that becomes the new local 0,0")
    height: StringProperty(
        name="Height",
        description="Survey height that becomes local Z=0. Keep 0 so storeys keep their real heights",
        default="0",
    )
    output_path: StringProperty(
        name="Save As",
        description="The localized copy. The open IFC file is never overwritten",
        subtype="FILE_PATH",
    )

    if TYPE_CHECKING:
        crs_name: str
        easting: str
        northing: str
        height: str
        output_path: str

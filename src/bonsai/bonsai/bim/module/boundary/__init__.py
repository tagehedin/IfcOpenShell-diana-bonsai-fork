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

import bpy

from . import decorator, operator, prop, ui

classes = (
    operator.AddBoundary,
    operator.ColourByRelatedBuildingElement,
    operator.DecorateBoundaries,
    operator.DisableEditingBoundary,
    operator.DisableEditingBoundaryGeometry,
    operator.EditBoundaryAttributes,
    operator.EditBoundaryGeometry,
    operator.EnableEditingBoundary,
    operator.EnableEditingBoundaryGeometry,
    operator.HideBoundaries,
    operator.LoadBoundary,
    operator.LoadProjectSpaceBoundaries,
    operator.LoadSpaceBoundaries,
    operator.SelectProjectBoundaries,
    operator.SelectRelatedElementBoundaries,
    operator.SelectRelatedElementTypeBoundaries,
    operator.SelectSpaceBoundaries,
    operator.ShowBoundaries,
    operator.UpdateBoundaryGeometry,
    ui.BIM_PT_Boundary,
    ui.BIM_PT_SceneBoundaries,
    ui.BIM_PT_SpaceBoundaries,
    prop.BIMBoundaryProperties,
    prop.BIMObjectBoundaryProperties,
)


def register():
    bpy.types.Scene.BIMBoundaryProperties = bpy.props.PointerProperty(type=prop.BIMBoundaryProperties)
    bpy.types.Object.BIMBoundaryProperties = bpy.props.PointerProperty(type=prop.BIMObjectBoundaryProperties)


def unregister():
    # Uninstall before deleting the properties it reads - see project/__init__.py's
    # unregister() for the full rationale (Blender's Extensions "Update" can
    # unregister/register in-place without a process restart, and a still-installed
    # decorator keeps firing its draw_handler_add callback during that window).
    decorator.BoundaryDecorator.uninstall()
    del bpy.types.Scene.BIMBoundaryProperties
    del bpy.types.Object.BIMBoundaryProperties

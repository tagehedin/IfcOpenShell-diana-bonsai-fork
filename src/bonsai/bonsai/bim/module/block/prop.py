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

from typing import TYPE_CHECKING, Union

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup

import bonsai.tool as tool


class BIMBlockDefinition(PropertyGroup):
    name: StringProperty(name="Name")

    if TYPE_CHECKING:
        name: str


class BIMBlockProperties(PropertyGroup):
    block_definitions: CollectionProperty(name="Block Definitions", type=BIMBlockDefinition)
    active_block_index: IntProperty(name="Active Block Index", default=0)
    # bim_block_id of the instance currently being edited, or "" if none
    editing_instance_id: StringProperty(name="Editing Instance ID", default="")

    if TYPE_CHECKING:
        block_definitions: bpy.types.bpy_prop_collection_idprop[BIMBlockDefinition]
        active_block_index: int
        editing_instance_id: str

    @property
    def active_block(self) -> Union[BIMBlockDefinition, None]:
        return tool.Blender.get_active_uilist_element(self.block_definitions, self.active_block_index)

    @property
    def is_editing(self) -> bool:
        return bool(self.editing_instance_id)

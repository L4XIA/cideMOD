#
# Copyright (c) 2022 CIDETEC Energy Storage.
#
# This file is part of cideMOD.
#
# cideMOD is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
from .basic_mechanics import BaseMechanicalModel

class LiquidElectrolyteMechanicalModel(BaseMechanicalModel):
    def __init__(self, cell) -> None:
        pass

    def fields(self):
        return []

    def storage_order(self):
        return []

    def shape_functions(self, mesh):
        return []

    def displacement_wf(self, *args, **kwargs):
        return 0

    def hydrostatic_stress_wf(self, *args, **kwargs):
        return 0

    def fixed_bc(self, *args, **kwargs):
        return []

    def slip_bc(self, *args, **kwargs):
        return []

    def pressure_bc(self, *args, **kwargs):
        return []
        

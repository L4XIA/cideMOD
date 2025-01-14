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
from dolfin import *

from typing import List

import numpy
from numpy.polynomial.legendre import *

from cideMOD.numerics.polynomials import Lagrange
from cideMOD.helpers.miscellaneous import project_onto_subdomains
from numpy.polynomial.polynomial import *
from ufl.operators import exp, sinh

__all__= [
    "SEI",
    "LAM"
]

class LAM:
    r"""
    Loss of Active Material model from [1]_ and [2]_, compute the lost of active material due to particle cracking driven by stresses.

    .. note::

        This model assumes that between cycles, the particle reach a steady state without stress. :math:`\sigma_{h,min} = 0`.

    References
    ----------
    .. [1] X. Zhang, W. Shyy & A. M. Sastry (2007) Numerical Simulation of Intercalation-Induced Stress in 
           Li-Ion Battery Electrode Particles. Journal of Electrochemical Society. 154 A910

    .. [2] J. M. Reniers, G. Mulder & D. A. Howey (2019) Review and Performance Comparison of Mechanical-Chemical 
           Degradation Models for Lithium-Ion Batteries. Journal of Electrochemical Society. 166 A3189
    """
    def __init__(self, tag):
        assert tag in ['anode', 'cathode']
        self.tag = tag
        self.LAM = None

    def setup(self, problem):
        self.electrode = getattr(problem, self.tag)
        self.LAM = self.electrode.LAM

        # Compute eps_s variation
        if problem.c_s_implicit_coupling:
            c_s_r_average = problem.SGM.c_s_r_average(problem.f_1, self.tag)
            c_s_surf = problem.SGM.c_s_surf(problem.f_1, self.tag)
        else:
            if self.tag == 'anode':
                c_s_surf = problem.c_s_surf_1_anode
                self.particle_model = problem.anode_particle_model
                self.electrode_dofs = problem.anode_dofs
            else:
                c_s_surf = problem.c_s_surf_1_cathode
                self.particle_model = problem.cathode_particle_model
                self.electrode_dofs = problem.cathode_dofs
            self.c_s_r_average = c_s_r_average = [Function(problem.V) for _ in self.electrode.active_material]

        # if 'sigma_h' in problem.f_1._fields:
        #     self.sigma_h = problem.f_1._asdict()['sigma_h']
        self.sigma_h = self.hydrostatic_stress(c_s_r_average, c_s_surf)

        self.delta_eps_s = self.eps_s_variation(self.sigma_h, problem.DT.delta_t)
        
    def eps_s_variation(self, sigma_h, delta_t):
        delta_eps_s = []
        for i, am in enumerate(self.electrode.active_material):
            value = 0
            if self.LAM.model == 'stress': 
                sigma_h_am = conditional(gt(sigma_h[i],0), sigma_h[i], Constant(0.)) # hydrostatic compressive stress makes no contribution
                value -= delta_t* self.LAM.beta * (sigma_h_am/am.critical_stress)**self.LAM.m
            delta_eps_s.append(value)
        return delta_eps_s

    def approximation(self, dx):
        delta_eps_s = []
        for i, am in enumerate(self.electrode.active_material):
            value = 0
            if self.LAM.model == 'stress':
                value = assemble(self.delta_eps_s[i]*dx)
            delta_eps_s.append(value)
        return delta_eps_s

    def hydrostatic_stress(self, c_s_r_average, c_s):
        sigma_h = []
        for i, am in enumerate(self.electrode.active_material):
            sigma_h.append( 2/9*am.omega*am.young/(1-am.poisson)*(3*c_s_r_average[i] - c_s[i]) )
        return sigma_h

    def update_eps_s(self, problem):
        for i, delta_eps_s_am in enumerate(self.delta_eps_s):
            eps_s_am = self.electrode.active_material[i].eps_s
            eps_s_am.assign(project(eps_s_am + delta_eps_s_am, V = problem.V))
            # eps_s_am.assign(project_onto_subdomains({self.tag:eps_s_am + delta_eps_s_am}, problem, V = problem.V_0))

    def _update_c_s_r_average(self):
        """
        Once the particle models problem are solved the macroscopic model needs to be updated.
        This function update the R-average concentration on the particles.
        """
        c_s_r_average_array = self.particle_model.get_average_c_s()
        for i in range(len(self.electrode.active_material)):
            self.c_s_r_average[i].vector()[self.electrode_dofs] = c_s_r_average_array[:, i]

    def __bool__(self):
        return bool(self.LAM)


class SEI:
    """
    SEI (Solid-Electrolyte Interphase) growth model limited by solvent diffusion through the SEI.

    Args:
        order: Order of the inner spectral model for solvent diffusion, default is 2.

    References:
        1: Safari et al. - 2009 - Multimodal Physics-Based Aging Model for Life Prediction of Li-Ion Batteries
    """
    def __init__(self, tag, order=2):
        assert tag in ['anode','cathode']
        self.domain = tag[0]
        self.tag = tag
        self.order = order
        self.SEI = None

    def setup(self, problem):
        self.electrode = getattr(problem, self.tag)
        self.SEI = self.electrode.SEI
        self.n_mat = len(self.electrode.active_material)
        self.SLagM = self.SpectralLagrangeModel_EC(self.n_mat, self.tag, self.order)
        self.j_Li = problem.j_Li_a if self.tag == 'anode' else problem.j_Li_c

    def fields(self, n_mat = None):
        if self.SEI is None:
            assert n_mat is not None, "Please provide the number of active materials or setup the SEI object"
        else:
            n_mat = self.n_mat

        field_list  = [f'j_sei_{self.domain}{material}' for material in range(n_mat)]
        field_list += [f'delta_sei_{self.domain}{material}' for material in range(n_mat)]
        field_list += [f'c_EC_{order}_{self.domain}{material}' for material in range(n_mat) for order in range(self.order)]
        return field_list

    def shape_functions(self, mesher, n_mat, V = None):
        if V is None:
            V = FunctionSpace(mesher.mesh, 'CG', 1)
        restriction = mesher.anode if self.tag == 'anode' else mesher.cathode
        E_j_sei = [(V, restriction) for material in range(n_mat)]
        E_delta = [(V, restriction) for material in range(n_mat)]
        E_c_EC  = [(V, restriction) for material in range(n_mat) for order in range(self.order)]
        return E_j_sei + E_delta + E_c_EC

    def initial_guess(self, f_0):
        if self.n_mat!=0:
            c_0 = self.SEI.c_EC_sln * self.SEI.eps
            for material in range(self.n_mat):
                c_EC_index = f_0._fields.index(f'c_EC_0_{self.domain}{material}')
                for j in range(self.order):
                    assign(f_0[c_EC_index+j], interpolate(Constant(c_0), f_0[c_EC_index+j].function_space()))

                delta_0_am = f_0._asdict()[f'delta_sei_{self.domain}{material}']
                assign(delta_0_am, interpolate(Constant(self.SEI.delta0), delta_0_am.function_space()))

    def j_SEI(self, j_SEI, test, dx, i_0s, phi_s, phi_e, T, J, G_film, SEI, F, R):

        eta = self.SEI_overpotential(phi_s, phi_e, J, G_film, SEI)

        return j_SEI * test * dx + i_0s * exp(-(SEI.beta * F / R) * eta / T) * test * dx

    def SEI_overpotential(self, phi_s, phi_e, J, G_film, SEI):

        return phi_s - phi_e - SEI.U - J * G_film

    def delta_growth(self, DT, delta_0, delta_1, j_SEI, SEI, F, test, dx):
        
        if DT:
            return DT.dt(delta_0, delta_1) * test * dx + j_SEI * SEI.M / (2 * F * SEI.rho) * test * dx
        else:
            return (delta_0 - delta_1) * test * dx

    def equations(self, f_0, f_1, test, dx, F, R, DT = None):
        F_SEI = []
        for i, material in enumerate(self.electrode.active_material):
            j_sei_index = f_1._fields.index(f'j_sei_{self.domain}{i}')
            delta_index = f_1._fields.index(f'delta_sei_{self.domain}{i}')
            c_EC_index = f_1._fields.index(f'c_EC_0_{self.domain}{i}')
            
            i_0s = F * f_1[c_EC_index] * self.SEI.k_f_s
            G_film = self.SEI.R + f_1[delta_index] / self.SEI.kappa
            J = self.j_Li.total[i]
            F_SEI.append(self.j_SEI(f_1[j_sei_index], test[j_sei_index], dx, i_0s, f_1.phi_s, f_1.phi_e, f_1.temp, J, G_film, self.SEI, F, R))
            F_SEI.append(self.delta_growth(DT, f_0[delta_index], f_1[delta_index], f_1[j_sei_index], self.SEI, F ,test[delta_index], dx))

        if DT:
            F_SEI.extend(self.SLagM.wf(f_0, f_1, test, dx, DT, self.SEI, F))
        else:
            F_SEI.extend(self.SLagM.wf_0(f_0, f_1, test, dx))
        
        return F_SEI

    def __bool__(self):
        return bool(self.SEI)

    class SpectralLagrangeModel_EC():
        
        def __init__(self, n_mat, tag, order=2):
            assert tag in ('anode','cathode')
            self.domain = tag[0]
            self.tag = tag
            self.order = order
            self.n_mat = n_mat
            self.poly = Lagrange(order)
            self.f = self.poly.f
            self.df = self.poly.df
            self.xf = self.poly.xf
            self.xdf = self.poly.xdf
            self.build_matrix()

        def build_matrix(self):
            J = numpy.zeros((self.order+1, self.order+1))
            H = numpy.zeros((self.order+1, self.order+1))
            K = numpy.zeros((self.order+1, self.order+1))
            L = numpy.zeros((self.order+1, self.order+1))
            M = numpy.zeros((self.order+1, self.order+1))
            N = numpy.zeros((self.order+1, self.order+1))
            P = numpy.zeros(self.order+1)

            for i in range(self.order+1):

                P[i] = polyval(0, self.f[i])

                for j in range(self.order+1):

                    J[i,j] = polyval(0, polymul(self.f[i], self.f[j]))
                    H[i,j] = polyval(1, polymul(self.df[i], self.f[j]))
                    K[i,j] = polyval(1,polyint(polymul(self.f[i], self.f[j])))
                    L[i,j] = polyval(1,polyint(polymul(self.df[i], self.f[j])))
                    M[i,j] = polyval(1,polyint(polymul(self.xdf[i], self.f[j])))
                    N[i,j] = polyval(1,polyint(polymul(self.df[i], self.df[j])))

            J_d = J[:, 0:-1]
            K_d = K[:, 0:-1]
            L_d = L[:, 0:-1]
            M_d = M[:, 0:-1]
            N_d = N[:, 0:-1]
            H_d = H[:, 0:-1]
            P_d = P

            self.D = K_d
            self.K1 = L_d - M_d + J_d
            self.K2 = N_d - H_d
            self.P = P_d

        def wf_0(self, f_0, f_1, test, dx):
            F_EC_0 = []
            for material in range(self.n_mat):
                c_EC_index = f_0._fields.index(f'c_EC_0_{self.domain}{material}')
                for j in range(self.order):
                    F_EC_0.append([(f_1[c_EC_index+j] - f_0[c_EC_index+j]) * test[c_EC_index+j] * dx])
            return F_EC_0

        def wf(self, f_0, f_1, test, dx:Measure, DT, SEI, F):
            F_EC_ret = []
            for k in range(self.n_mat):
                c_EC_index = f_0._fields.index(f'c_EC_0_{self.domain}{k}')
                c_EC_0 = [f_0[c_EC_index+j] for j in range(self.order)] + [SEI.c_EC_sln * SEI.eps]
                c_EC = [f_1[c_EC_index+j] for j in range(self.order)] + [SEI.c_EC_sln * SEI.eps]
                j_SEI_index = f_0._fields.index(f'j_sei_{self.domain}{k}')
                delta_index = f_0._fields.index(f'delta_sei_{self.domain}{k}')
                self.K = 1 / f_1[delta_index] * DT.dt(f_0[delta_index], f_1[delta_index]) * self.K1 + 1 / f_1[delta_index]**2 * SEI.D_EC * self.K2
                for j in range(self.order):
                    F_EC = 0
                    for i in range(self.order+1):
                        F_EC += self.D[i, j] * DT.dt(c_EC_0[i], c_EC[i]) * test[c_EC_index+j] * dx(metadata={"quadrature_degree":2})
                        F_EC += self.K[i, j] * c_EC[i] * test[c_EC_index+j] * dx(metadata={"quadrature_degree":2})

                    F_EC -= 1 / f_1[delta_index] * f_1[j_SEI_index] / F * self.P[j] * test[c_EC_index+j] * dx(metadata={"quadrature_degree":2})
                    
                    F_EC_ret.append(F_EC)

            return F_EC_ret

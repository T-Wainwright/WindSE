"""
The ProblemManager contains all of the 
different classes of problems that windse can solve
"""

import __main__
import os

### Get the name of program importing this package ###
main_file = os.path.basename(__main__.__file__)

### This checks if we are just doing documentation ###
if main_file != "sphinx-build":
    from dolfin import *
    import numpy as np
    import time

    ### Import the cumulative parameters ###
    from windse import windse_parameters

    ### Check if we need dolfin_adjoint ###
    if windse_parameters["general"].get("dolfin_adjoint", False):
        from dolfin_adjoint import *

class GenericProblem(object):
    """
    A GenericProblem contains on the basic functions required by all problem objects.
    
    Args: 
        domain (:meth:`windse.DomainManager.GenericDomain`): a windse domain object.
        windfarm (:meth:`windse.WindFarmManager.GenericWindFarmm`): a windse windfarm object.
        function_space (:meth:`windse.FunctionSpaceManager.GenericFunctionSpace`): a windse function space object.
        boundary_conditions (:meth:`windse.BoundaryManager.GenericBoundary`): a windse boundary object.
    """
    def __init__(self,domain,windfarm,function_space,boundary_data):
        ### save a reference of option and create local version specifically of domain options ###
        self.params = windse_parameters
        self.dom  = domain
        self.farm = windfarm
        self.fs   = function_space 
        self.bd  = boundary_data
        self.tf_first_save = True
        self.fprint = self.params.fprint

        ### Append the farm ###
        self.params.full_farm = self.farm

        ### add some helper items for dolfin_adjoint_helper.py ###
        self.params.ground_fx = self.dom.Ground
        self.params.full_hh = self.farm.HH

    def ChangeWindAngle(self,theta):
        """
        This function recomputes all necessary components for a new wind direction

        Args: 
            theta (float): The new wind angle in radians
        """
        adj_start = time.time()
        self.fprint("Adjusting Wind Angle",special="header")
        self.fprint("New Angle: {:1.8f} rads".format(theta))
        self.dom.RecomputeBoundaryMarkers(theta)
        self.bd.RecomputeVelocity(theta)
        self.ComputeFunctional(theta)

        adj_stop = time.time()
        self.fprint("Wind Angle Adjusted: {:1.2f} s".format(adj_stop-adj_start),special="footer")

        # self.tf.assign(tf_temp)

    def ChangeWindSpeed(self,speed):
        adj_start = time.time()
        self.fprint("Adjusting Wind Speed",special="header")
        self.fprint("New Speed: {:1.8f} m/s".format(speed))
        self.bd.HH_vel = speed
        adj_stop = time.time()
        self.fprint("Wind Speed Adjusted: {:1.2f} s".format(adj_stop-adj_start),special="footer")

class StabilizedProblem(GenericProblem):
    """
    The StabilizedProblem setup everything required for solving Navier-Stokes with 
    a stabilization term

    Args: 
        domain (:meth:`windse.DomainManager.GenericDomain`): a windse domain object.
        windfarm (:meth:`windse.WindFarmManager.GenericWindFarmm`): a windse windfarm object.
        function_space (:meth:`windse.FunctionSpaceManager.GenericFunctionSpace`): a windse function space object.
        boundary_conditions (:meth:`windse.BoundaryManager.GenericBoundary`): a windse boundary object.
    """
    def __init__(self,domain,windfarm,function_space,boundary_conditions):
        super(StabilizedProblem, self).__init__(domain,windfarm,function_space,boundary_conditions)
        
        ### Create Functional ###
        self.ComputeFunctional()


    def ComputeFunctional(self,theta=None):
        self.fprint("Setting Up Stabilized Problem",special="header")

        ### Create the test/trial/functions ###
        self.up_next = Function(self.fs.W)
        self.u_next,self.p_next = split(self.up_next)
        v,q = TestFunctions(self.fs.W)


        ### Set the initial guess ###
        ### (this will become a separate function.)
        self.up_next.assign(self.bd.u0)

        ### Create the turbine force ###
        if theta is not None:
            self.tf = self.farm.TurbineForce(self.fs,self.dom.mesh,self.u_next,delta_yaw=(theta-self.dom.init_wind))
            # self.tf1, self.tf2, self.tf3 = self.farm.TurbineForce(self.fs,self.dom.mesh,self.u_next,delta_yaw=(theta-self.dom.init_wind))
        else:
            self.tf = self.farm.TurbineForce(self.fs,self.dom.mesh,self.u_next)
            # self.tf1, self.tf2, self.tf3 = self.farm.TurbineForce(self.fs,self.dom.mesh,self.u_next)
            
        # self.tf = self.tf1*self.u_next[0]**2+self.tf2*self.u_next[1]**2+self.tf3*self.u_next[0]*self.u_next[1]

        ### These constants will be moved into the params file ###
        nu = self.params["problem"].get("viscosity",.1)
        lmax = self.params["problem"].get("lmax",15)
        f = Constant((0.0,)*self.dom.dim)
        vonKarman=0.41
        eps=Constant(1.0)

        self.fprint("Viscosity:                 {:1.2e}".format(float(nu)))
        self.fprint("Max Mixing Length:       {:1.2e}".format(float(lmax)))
        self.fprint("Stabilization Coefficient: {:1.2e}".format(float(eps)))

        ### Calculate the stresses and viscosities ###
        S = sqrt(2*inner(0.5*(grad(self.u_next)+grad(self.u_next).T),0.5*(grad(self.u_next)+grad(self.u_next).T)))

        ### Create l_mix based on distance to the ground ###
        if self.dom.dim == 3:
            ### https://doi.org/10.5194/wes-4-127-2019###
            l_mix = Function(self.fs.Q)
            l_mix.vector()[:] = np.divide(vonKarman*self.bd.depth.vector()[:],(1.+np.divide(vonKarman*self.bd.depth.vector()[:],lmax)))
        else:
            l_mix = Constant(vonKarman*self.farm.HH[0]/(1+(vonKarman*self.farm.HH[0]/lmax)))
        
        ### Calculate nu_T
        self.nu_T=l_mix**2.*S

        ### Create the functional ###
        # if self.farm.yaw[0]**2 > 1e-4:
        #     self.F = inner(grad(self.u_next)*self.u_next, v)*dx + (nu+self.nu_T)*inner(grad(self.u_next), grad(v))*dx - inner(div(v),self.p_next)*dx - inner(div(self.u_next),q)*dx - inner(f,v)*dx + inner(self.tf,v)*dx 
        # else :
        self.F = inner(grad(self.u_next)*self.u_next, v)*dx + (nu+self.nu_T)*inner(grad(self.u_next), grad(v))*dx - inner(div(v),self.p_next)*dx - inner(div(self.u_next),q)*dx - inner(f,v)*dx + inner(self.tf,v)*dx 
        # self.F_sans_tf =  (1.0)*inner(grad(self.u_next), grad(v))*dx - inner(div(v),self.p_next)*dx - inner(div(self.u_next),q)*dx - inner(f,v)*dx
        # self.F = inner(grad(self.u_next)*self.u_next, v)*dx + (nu+self.nu_T)*inner(grad(self.u_next), grad(v))*dx - inner(div(v),self.p_next)*dx - inner(div(self.u_next),q)*dx - inner(f,v)*dx + inner(self.tf*(self.u_next[0]**2+self.u_next[1]**2),v)*dx 

        ### Add in the Stabilizing term ###
        # stab = - eps*inner(grad(q), grad(self.p_next))*dx - eps*inner(grad(q), dot(grad(self.u_next), self.u_next))*dx 
        stab = - eps*inner(grad(q), grad(self.p_next))*dx - eps*inner(grad(q), dot(grad(self.u_next), self.u_next))*dx 
        # stab_sans_tf = - eps*inner(grad(q), grad(self.p_next))*dx 

        self.F += stab
        # self.F_sans_tf += stab

        use_25d = self.params["problem"].get("use_25d_model", False)

        if use_25d and self.dom.dim == 2 :
            if self.dom.dim == 3:
                raise ValueError("The 2.5D model requires a 2D simulation.")

            self.fprint("Using 2.5D model")
            dudx = Dx(self.u_next[0], 0)
            dvdy = Dx(self.u_next[1], 1)

            term25 = (sin(self.dom.init_wind)*dudx*q + cos(self.dom.init_wind)*dvdy*q)*dx

            self.F -= term25

        self.fprint("Stabilized Problem Setup",special="footer")


class TaylorHoodProblem(GenericProblem):
    """
    The TaylorHoodProblem sets up everything required for solving Navier-Stokes 

    Args: 
        domain (:meth:`windse.DomainManager.GenericDomain`): a windse domain object.
        windfarm (:meth:`windse.WindFarmManager.GenericWindFarmm`): a windse windfarm object.
        function_space (:meth:`windse.FunctionSpaceManager.GenericFunctionSpace`): a windse function space object.
        boundary_conditions (:meth:`windse.BoundaryManager.GenericBoundary`): a windse boundary object.
    """
    def __init__(self,domain,windfarm,function_space,boundary_conditions):
        super(TaylorHoodProblem, self).__init__(domain,windfarm,function_space,boundary_conditions)

        ### Create Functional ###
        self.ComputeFunctional()

    def ComputeFunctional(self,theta=None):
        self.fprint("Setting Up Taylor-Hood Problem",special="header")

        ### These constants will be moved into the params file ###
        nu = self.params["problem"].get("viscosity",0.1)
        lmax = self.params["problem"].get("lmax",15)
        f = Constant((0.0,)*self.dom.dim)
        vonKarman=0.41
        eps=Constant(0.01)

        self.fprint("Viscosity:           {:1.2e}".format(float(nu)))
        self.fprint("Max Mixing Length:       {:1.2e}".format(float(lmax)))

        ### Create the test/trial/functions ###
        self.up_next = Function(self.fs.W)
        self.u_next,self.p_next = split(self.up_next)
        v,q = TestFunctions(self.fs.W)

        ### Set the initial guess ###
        ### (this will become a separate function.)
        self.up_next.assign(self.bd.u0)

        ### Calculate the stresses and viscosities ###
        S = sqrt(2.*inner(0.5*(grad(self.u_next)+grad(self.u_next).T),0.5*(grad(self.u_next)+grad(self.u_next).T)))

        ### Create l_mix based on distance to the ground ###
        if self.dom.dim == 3:
            ### https://doi.org/10.5194/wes-4-127-2019###
            l_mix = Function(self.fs.Q)
            l_mix.vector()[:] = np.divide(vonKarman*self.bd.depth.vector()[:],(1.+np.divide(vonKarman*self.bd.depth.vector()[:],lmax)))
        else:
            l_mix = Constant(vonKarman*self.farm.HH[0]/(1+(vonKarman*self.farm.HH[0]/lmax)))

        ### Calculate nu_T
        self.nu_T=l_mix**2.*S

        ### Create the turbine force ###
        if theta is not None:
            self.tf = self.farm.TurbineForce(self.fs,self.dom.mesh,self.u_next,delta_yaw=(theta-self.dom.init_wind))
            # self.tf1, self.tf2, self.tf3 = self.farm.TurbineForce(self.fs,self.dom.mesh,self.u_next,delta_yaw=(theta-self.dom.init_wind))
        else:
            self.tf = self.farm.TurbineForce(self.fs,self.dom.mesh,self.u_next)
            # self.tf1, self.tf2, self.tf3 = self.farm.TurbineForce(self.fs,self.dom.mesh,self.u_next)
            
        # self.tf = self.tf1*self.u_next[0]**2+self.tf2*self.u_next[1]**2+self.tf3*self.u_next[0]*self.u_next[1]

        ### Create the functional ###
        self.F = inner(grad(self.u_next)*self.u_next, v)*dx + (nu+self.nu_T)*inner(grad(self.u_next), grad(v))*dx - inner(div(v),self.p_next)*dx - inner(div(self.u_next),q)*dx - inner(f,v)*dx + inner(self.tf,v)*dx 
    
        use_25d = self.params["problem"].get("use_25d_model", False)

        if use_25d:
            if self.dom.dim == 3:
                raise ValueError("The 2.5D model requires a 2D simulation.")

            self.fprint("Using 2.5D model")
            dudx = Dx(self.u_next[0], 0)
            dvdy = Dx(self.u_next[1], 1)

            term25 = (sin(self.dom.init_wind)*dudx*q + cos(self.dom.init_wind)*dvdy*q)*dx

            self.F -= term25

        self.fprint("Taylor-Hood Problem Setup",special="footer")

import torch
import numpy as np
import logging
from .classical import get_propagation_kernel
from .util import wavenumber, generate_complex_field, calculate_amplitude, calculate_phase
from ..tools import zero_pad, crop_center, circular_binary_mask


class propagator():
    """
    A light propagation model that propagates light to desired image plane with two separate propagations. 
    We use this class in our various works including `Kavaklı et al., Realistic Defocus Blur for Multiplane Computer-Generated Holography`.
    """
    def __init__(
                 self,
                 resolution = [1920, 1080],
                 wavelengths = [515e-9,],
                 pixel_pitch = 8e-6,
                 resolution_factor = 1,
                 number_of_frames = 1,
                 number_of_depth_layers = 1,
                 volume_depth = 1e-2,
                 image_location_offset = 5e-3,
                 propagation_type = 'Bandlimited Angular Spectrum',
                 propagator_type = 'back and forth',
                 back_and_forth_distance = 0.3,
                 laser_channel_power = None,
                 aperture = None,
                 device = torch.device('cpu')
                ):
        """
        Parameters
        ----------
        resolution              : list
                                  Resolution.
        wavelength              : float
                                  Wavelength of light in meters.
        pixel_pitch             : float
                                  Pixel pitch in meters.
        resolution_factor       : int
                                  Resolution factor for scaled simulations.
        number_of_frames        : int
                                  Number of hologram frames.
                                  Typically, there are three frames, each one for a single color primary.
        number_of_depth_layers  : int
                                  Equ-distance number of depth layers within the desired volume.
        volume_depth            : float
                                  Width of the volume along the propagation direction.
        image_location_offset   : float
                                  Center of the volume along the propagation direction.
        propagation_type        : str
                                  Propagation type. 
                                  See ropagate_beam() and odak.learn.wave.get_propagation_kernel() for more.
        propagator_type         : str
                                  Propagator type.
                                  The options are `back and forth` and `forward` propagators.
        back_and_forth_distance : float
                                  Zero mode distance for `back and forth` propagator type.
        laser_channel_power     : torch.tensor
                                  Laser channel powers for given number of frames and number of wavelengths.
        aperture                : torch.tensor
                                  Aperture at the Fourier plane.
        device                  : torch.device
                                  Device to be used for computation. For more see torch.device().
        """
        self.device = device
        self.pixel_pitch = pixel_pitch
        self.wavelengths = wavelengths
        self.resolution = resolution
        self.resolution_factor = resolution_factor
        self.number_of_frames = number_of_frames
        self.number_of_depth_layers= number_of_depth_layers
        self.number_of_channels = len(self.wavelengths)
        self.volume_depth = volume_depth
        self.image_location_offset = image_location_offset
        self.propagation_type = propagation_type
        self.propagator_type = propagator_type
        self.zero_mode_distance = back_and_forth_distance
        self.aperture = aperture
        self.init_distances()
        self.init_kernels()
        self.init_channel_power(laser_channel_power)
        self.init_phase_scale()
        self.set_aperture(aperture)


    def init_distances(self):
        """
        Internal function to initialize distances.
        """
        self.distances = torch.linspace(-self.volume_depth / 2., self.volume_depth / 2., self.number_of_depth_layers) + self.image_location_offset
        logging.warning('Distances: {}'.format(self.distances))


    def init_kernels(self):
        """
        Internal function to initialize kernels.
        """
        self.generated_kernels = torch.zeros(
                                             self.number_of_depth_layers,
                                             self.number_of_channels,
                                             device = self.device
                                            )
        self.kernels = torch.zeros(
                                   self.number_of_depth_layers,
                                   self.number_of_channels,
                                   self.resolution[0] * self.resolution_factor * 2,
                                   self.resolution[1] * self.resolution_factor * 2,
                                   dtype = torch.complex64,
                                   device = self.device
                                  )
        self.kernel_components = torch.zeros(
                                             self.number_of_depth_layers,
                                             self.number_of_channels,
                                             2,
                                             self.resolution[0] * self.resolution_factor * 2,
                                             self.resolution[1] * self.resolution_factor * 2,
                                             device = self.device
                                            )


    def init_channel_power(self, channel_power):
        """
        Internal function to set the starting phase of the phase-only hologram.
        """
        self.channel_power = channel_power
        if isinstance(self.channel_power, type(None)):
            self.channel_power = torch.eye(
                                           self.number_of_frames,
                                           self.number_of_channels,
                                           device = self.device,
                                           requires_grad = False
                                          )


    def init_phase_scale(self):
        """
        Internal function to set the phase scale.
        In some cases, you may want to modify this init to ratio phases for different color primaries as an SLM is configured for a specific central wavelength.
        """
        self.phase_scale = torch.tensor(
                                        [
                                         1.,
                                         1.,
                                         1.
                                        ],
                                        requires_grad = False,
                                        device = self.device
                                       )


    def set_aperture(self, aperture = None, aperture_size = None):
        """
        Set aperture in the Fourier plane.


        Parameters
        ----------
        aperture        : torch.tensor
                          Aperture at the original resolution of a hologram.
                          If aperture is provided as None, it will assign a circular aperture at the size of the short edge (width or height).
        aperture_size   : int
                          If no aperture is provided, this will determine the size of the circular aperture.
        """
        if isinstance(aperture, type(None)):
            if isinstance(aperture_size, type(None)):
                aperture_size = torch.max(
                                          torch.tensor([
                                                        self.resolution[0] * self.resolution_factor, 
                                                        self.resolution[1] * self.resolution_factor
                                                       ])
                                         )
            self.aperture = circular_binary_mask(
                                                 self.resolution[0] * self.resolution_factor * 2,
                                                 self.resolution[1] * self.resolution_factor * 2,
                                                 aperture_size,
                                                ).to(self.device) * 1.
        else:
            self.aperture = zero_pad(aperture).to(self.device) * 1.


    def get_laser_powers(self):
        """
        #Internal function to get the laser powers.

        Returns
        -------
        laser_power      : torch.tensor
                           Laser powers.
        """
        laser_power = self.channel_power
        return laser_power



    def set_laser_powers(self, laser_power):
        """
        Internal function to set the laser powers.

        Parameters
        -------
        laser_power      : torch.tensor
                           Laser powers.
        """
        self.channel_power = laser_power



    def get_kernels(self):
        """
        Function to return the kernels used in the light transport.
        
        Returns
        -------
        kernels           : torch.tensor
                            Kernel amplitudes.
        """
        h = torch.fft.ifftshift(torch.fft.ifft2(torch.fft.ifftshift(self.kernels)))
        kernels_amplitude = calculate_amplitude(h)
        kernels_phase = calculate_phase(h)
        return kernels_amplitude, kernels_phase


    def propagate(self, field, H):
        """
        Internal function used in propagation. It is a copy of odak.learn.wave.band_limited_angular_spectrum().
        """
        field_padded = zero_pad(field)
        U1 = torch.fft.fftshift(torch.fft.fft2(torch.fft.fftshift(field_padded)))
        U2 = H * self.aperture * U1
        result_padded = torch.fft.ifftshift(torch.fft.ifft2(torch.fft.ifftshift(U2)))
        result = crop_center(result_padded)
        return result


    def __call__(self, input_field, channel_id, depth_id):
        """
        Function that represents the forward model in hologram optimization.

        Parameters
        ----------
        input_field         : torch.tensor
                              Input complex input field.
        channel_id          : int
                              Identifying the color primary to be used.
        depth_id            : int
                              Identifying the depth layer to be used.

        Returns
        -------
        output_field        : torch.tensor
                              Propagated output complex field.
        """
        distance = self.distances[depth_id]
        if not self.generated_kernels[depth_id, channel_id]:
            if self.propagator_type == 'forward':
                H = get_propagation_kernel(
                                           nu = input_field.shape[-2] * 2,
                                           nv = input_field.shape[-1] * 2,
                                           dx = self.pixel_pitch,
                                           wavelength = self.wavelengths[channel_id],
                                           distance = distance,
                                           device = self.device,
                                           propagation_type = self.propagation_type,
                                           scale = self.resolution_factor
                                          )
            elif self.propagator_type == 'back and forth':
                H_forward = get_propagation_kernel(
                                                   nu = input_field.shape[-2] * 2,
                                                   nv = input_field.shape[-1] * 2,
                                                   dx = self.pixel_pitch,
                                                   wavelength = self.wavelengths[channel_id],
                                                   distance = self.zero_mode_distance,
                                                   device = self.device,
                                                   propagation_type = self.propagation_type,
                                                   scale = self.resolution_factor
                                                  )
                distance_back = -(self.zero_mode_distance + self.image_location_offset - distance)
                H_back = get_propagation_kernel(
                                                nu = input_field.shape[-2] * 2,
                                                nv = input_field.shape[-1] * 2,
                                                dx = self.pixel_pitch,
                                                wavelength = self.wavelengths[channel_id],
                                                distance = distance_back,
                                                device = self.device,
                                                propagation_type = self.propagation_type,
                                                scale = self.resolution_factor
                                               )
                H = H_forward * H_back
            self.kernels[depth_id, channel_id] = H
            self.generated_kernels[depth_id, channel_id] = True
        else:
            H = self.kernels[depth_id, channel_id].detach().clone()
        output_field = self.propagate(input_field, H)
        return output_field

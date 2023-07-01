import torch
from .primitives import define_plane
from .boundary import intersect_w_surface
from ..tools import grid_sample


class detector():
    """
    A class to represent a detector.
    """


    def __init__(
                 self,
                 colors = 3,
                 center = torch.tensor([0., 0., 0.]),
                 tilt = torch.tensor([0., 0., 0.]),
                 size = torch.tensor([10., 10.]),
                 resolution = torch.tensor([100, 100]),
                 device = torch.device('cpu')
                ):
        """
        Parameters
        ----------
        colors         : int
                         Number of color channels to register (e.g., RGB).
        center         : torch.tensor
                         Center point of the detector [3].
        tilt           : torch.tensor
                         Tilt angles of the surface in degrees [3].
        size           : torch.tensor
                         Size of the detector [2].
        resolution     : torch.tensor
                         Resolution of the detector.
        device         : torch.device
                         Device for computation (e.g., cuda, cpu).
        """
        self.device = device
        self.colors = colors
        self.resolution = resolution.to(self.device)
        self.surface_center = center.to(self.device)
        self.surface_tilt = tilt.to(self.device)
        self.size = size.to(self.device)
        self.pixel_size = torch.tensor([
                                        self.size[0] / self.resolution[0],
                                        self.size[1] / self.resolution[1]
                                       ], device  = self.device)
        self.pixel_diagonal_size = torch.sqrt(self.pixel_size[0] ** 2 + self.pixel_size[1] ** 2)
        self.pixel_diagonal_half_size = self.pixel_diagonal_size / 2.
        self.threshold = torch.nn.Threshold(self.pixel_diagonal_size, 1)
        self.plane = define_plane(
                                  point = self.surface_center,
                                  angles = self.surface_tilt
                                 )
        self.pixel_locations, _, _, _ = grid_sample(
                                                    size = self.size.tolist(),
                                                    no = self.resolution.tolist(),
                                                    center = self.surface_center.tolist(),
                                                    angles = self.surface_tilt.tolist()
                                                   )
        self.pixel_locations = self.pixel_locations.to(self.device)
        self.clear()
        self.sigmoid = torch.nn.Sigmoid()
        self.threshold = torch.nn.Threshold(0.8, 0)


    def intersect(self, rays, color = 0):
        """
        Function to intersect rays with the detector


        Parameters
        ----------
        rays            : torch.tensor
                          Rays to be intersected with a detector.
                          Expected size is [1 x 2 x 3] or [m x 2 x 3].
        color           : int
                          Color channel to register.

        Returns
        -------
        image           : torch.tensor
                          Image on the image sensor [3 x k x l].
        """
        normals, _ = intersect_w_surface(rays, self.plane)
        points = normals[:, 0]
        #print(self.pixel_locations.shape);import sys;sys.exit()
        distances = torch.sqrt(torch.sum((points.unsqueeze(1) - self.pixel_locations.unsqueeze(0)) ** 2, dim = 2))
        #hit = distances * (distances < self.pixel_diagonal_half_size)        
        #hit = torch.nan_to_num(hit / hit, nan = 0.)
        hit = torch.abs(1. - 1. / (1. + torch.exp(- 50. * distances + 5)))
        image = torch.sum(hit, dim = 0)
        self.image[color] += image.reshape(self.image.shape[-2], self.image.shape[-1])
        return self.image, points


    def convert_image_to_points(self, image):
        image = image.reshape(image.shape[0], -1)
        print(image.shape, self.pixel_locations.shape)
        image_points = self.pixel_locations[image[0] > 0., :]
        return image_points


    def clear(self):
        """
        Internal function to clear a detector.
        """
        self.image = torch.zeros(
                                 self.colors,
                                 self.resolution[0],
                                 self.resolution[1],
                                 device = self.device,
                                )


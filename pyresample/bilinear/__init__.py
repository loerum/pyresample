#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2017

# Author(s):

#   Panu Lahtinen <panu.lahtinen@fmi.fi>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Code for resampling using bilinear algorithm for irregular grids.

The algorithm is taken from

http://www.ahinson.com/algorithms_general/Sections/InterpolationRegression/InterpolationIrregularBilinear.pdf

"""

import numpy as np
from pyproj import Proj

from pyresample import kd_tree


def resample_bilinear(data, in_area, out_area, radius=50e3,
                      neighbours=32, nprocs=1, fill_value=0):
    """Resample using bilinear interpolation.

    data : numpy array
        Array of single channel data points or
        (source_geo_def.shape, k) array of k channels of datapoints
    in_area : object or 2-tuple of numpy arrays
        Geometry definition of source data or tuple of (lons, lats)
    out_area : object
        Geometry definition of target area
    radius : float, optional
        Cut-off distance in meters
    neighbours : int, optional
        Number of neighbours to consider for each grid point when
        searching the closest corner points
    nprocs : int, optional
        Number of processor cores to be used for getting neighbour info
    fill_value : {int, None}, optional
            Set undetermined pixels to this value.
            If fill_value is None a masked array is returned
            with undetermined pixels masked
    """

    # Check in_area
    if isinstance(in_area, tuple):
        from pyresample.geometry import SwathDefinition
        lons, lats = _mask_coordinates(in_area[0], in_area[1])
        in_area = SwathDefinition(lons, lats)

    # Calculate the resampling information
    t__, s__, input_idxs, idx_ref = get_bil_info(in_area, out_area,
                                                 radius=radius,
                                                 neighbours=neighbours,
                                                 nprocs=nprocs,
                                                 masked=False)

    num_dsets = 1
    # Handle multiple datasets
    if data.ndim > 2 and data.shape[0] * data.shape[1] == input_idxs.shape[0]:
        num_dsets = data.shape[2]
        data = data.reshape(data.shape[0] * data.shape[1], data.shape[2])
    # Also ravel single dataset
    elif data.shape[0] != input_idxs.size:
        data = data.ravel()

    if num_dsets > 1:
        result = np.nan * np.zeros((out_area.size, num_dsets))
        for i in range(num_dsets):
            result[:, i] = get_sample_from_bil_info(data[:, i], t__, s__,
                                                    input_idxs, idx_ref,
                                                    output_shape=None)
    else:
        result = get_sample_from_bil_info(data[:, i], t__, s__,
                                          input_idxs, idx_ref,
                                          output_shape=None)

    if fill_value is None:
        result = np.ma.masked_where(np.isnan(result), result)
    else:
        result[result == np.nan] = fill_value

    return result


def get_sample_from_bil_info(data, t__, s__, input_idxs, idx_arr,
                             output_shape=None):
    """Resample data using bilinear interpolation.

    Parameters
    ----------
    data : numpy array
        1d array to be resampled
    t__ : numpy array
        Vertical fractional distances from corner to the new points
    s__ : numpy array
        Horizontal fractional distances from corner to the new points
    input_idxs : numpy array
        Valid indices in the input data
    idx_arr : numpy array
        Mapping array from valid source points to target points
    output_shape : tuple, optional
        Tuple of (y, x) dimension for the target projection.
        If None (default), do not reshape data.

    Returns
    -------
    result : numpy array
        Source data resampled to target geometry
    """

    # Reduce data
    new_data = data[input_idxs]
    data_min = np.nanmin(new_data)
    data_max = np.nanmax(new_data)

    new_data = new_data[idx_arr]

    # Get neighbour data to separate variables
    p_1 = new_data[:, 0]
    p_2 = new_data[:, 1]
    p_3 = new_data[:, 2]
    p_4 = new_data[:, 3]

    result = (p_1 * (1 - s__) * (1 - t__) +
              p_2 * s__ * (1 - t__) +
              p_3 * (1 - s__) * t__ +
              p_4 * s__ * t__)

    with np.errstate(invalid='ignore'):
        mask = ((result > data_max) | (result < data_min) |
                np.isnan(result) | result.mask)

    result = np.ma.masked_where(mask, result.data)

    if output_shape is not None:
        result = result.reshape(output_shape)

    return result


def get_bil_info(in_area, out_area, radius=50e3, neighbours=32, nprocs=1,
                 masked=False):
    """Calculate information needed for bilinear resampling.

    in_area : object or 2-tuple of numpy arrays
        Geometry definition of source data or tuple of (lons, lats)
    out_area : object
        Geometry definition of target area
    radius : float, optional
        Cut-off distance in meters
    neighbours : int, optional
        Number of neighbours to consider for each grid point when
        searching the closest corner points
    nprocs : int, optional
        Number of processor cores to be used for getting neighbour info
    masked : bool, optional
        If true, return masked arrays, else return np.nan values for
        invalid points (default)

    Returns
    -------
    t__ : numpy array
        Vertical fractional distances from corner to the new points
    s__ : numpy array
        Horizontal fractional distances from corner to the new points
    input_idxs : numpy array
        Valid indices in the input data
    idx_arr : numpy array
        Mapping array from valid source points to target points
    """

    # Calculate neighbour information
    (input_idxs, output_idxs, idx_ref, dists) = \
        kd_tree.get_neighbour_info(in_area, out_area,
                                   radius, neighbours=neighbours,
                                   nprocs=nprocs)

    del output_idxs, dists

    # Reduce index reference
    input_size = input_idxs.sum()
    index_mask = (idx_ref == input_size)
    idx_ref = np.where(index_mask, 0, idx_ref)

    # Get output projection as pyproj object
    proj = Proj(out_area.proj4_string)

    # Get output x/y coordinates
    out_x, out_y = _get_output_xy(out_area, proj)

    # Get input x/ycoordinates
    in_x, in_y = _get_input_xy(in_area, proj, input_idxs, idx_ref)

    # Get the four closest corner points around each output location
    pt_1, pt_2, pt_3, pt_4, idx_ref = \
        _get_bounding_corners(in_x, in_y, out_x, out_y, neighbours, idx_ref)

    # Calculate vertical and horizontal fractional distances t and s
    t__, s__ = _get_ts(pt_1, pt_2, pt_3, pt_4, out_x, out_y)

    # Remove mask and put np.nan at the masked locations instead
    if masked:
        mask = np.isnan(t__) | np.isnan(s__)
        t__ = np.ma.masked_where(mask, t__)
        s__ = np.ma.masked_where(mask, s__)

    return t__, s__, input_idxs, idx_ref


def _get_ts(pt_1, pt_2, pt_3, pt_4, out_x, out_y):
    """Calculate vertical and horizontal fractional distances t and s"""

    t__ = np.nan * np.zeros(out_y.shape)
    s__ = np.nan * np.zeros(out_y.shape)

    vert_parallel = _find_vert_parallels(pt_1, pt_2, pt_3, pt_4)
    horiz_parallel = _find_horiz_parallels(pt_1, pt_2, pt_3, pt_4)

    # Cases where verticals are parallel
    idxs = vert_parallel & np.invert(horiz_parallel)
    if np.any(idxs):
        t__[idxs], s__[idxs] = \
            _get_ts_uprights_parallel(pt_1[idxs, :], pt_2[idxs, :],
                                      pt_3[idxs, :], pt_4[idxs, :],
                                      out_y[idxs, :], out_x[idxs, :])

    # Cases where both verticals and horizontals are parallel
    idxs = vert_parallel & horiz_parallel
    if np.any(idxs):
        t__[idxs], s__[idxs] = \
            _get_ts_parallellogram(pt_1[idxs, :], pt_2[idxs, :], pt_3[idxs, :],
                                   out_y[idxs, :], out_x[idxs, :])

    # All the rest, ie. where the verticals are not parallel
    idxs = np.invert(vert_parallel)
    if np.any(idxs):
        t__[idxs], s__[idxs] = \
            _get_ts_irregular(pt_1[idxs, :], pt_2[idxs, :],
                              pt_3[idxs, :], pt_4[idxs, :],
                              out_y[idxs], out_x[idxs])

    with np.errstate(invalid='ignore'):
        idxs = (t__ < 0) | (t__ > 1) | (s__ < 0) | (s__ > 1)
    t__[idxs] = np.nan
    s__[idxs] = np.nan

    return t__, s__


def _find_vert_parallels(pt_1, pt_2, pt_3, pt_4):
    """Find vertical parallels from rectangle defined by four (x, y)
    points"""
    vals = (pt_3[:, 0] - pt_1[:, 0]) * (pt_4[:, 1] - pt_2[:, 1]) - \
           (pt_4[:, 0] - pt_2[:, 0]) * (pt_3[:, 1] - pt_1[:, 1])
    idxs = vals == 0.0

    return idxs


def _find_horiz_parallels(pt_1, pt_2, pt_3, pt_4):
    """Find horizontal parallels from rectangle defined by four (x, y)
    points"""
    vals = (pt_2[:, 0] - pt_1[:, 0]) * (pt_4[:, 1] - pt_3[:, 1]) - \
           (pt_4[:, 0] - pt_3[:, 0]) * (pt_2[:, 1] - pt_1[:, 1])
    idxs = vals == 0.0

    return idxs


def _get_ts_irregular(pt_1, pt_2, pt_3, pt_4, out_y, out_x):
    """Get parameters for the case where none of the sides are parallel."""

    # Pairwise longitudal separations between reference points
    x_21 = pt_2[:, 0] - pt_1[:, 0]
    x_31 = pt_3[:, 0] - pt_1[:, 0]
    x_42 = pt_4[:, 0] - pt_2[:, 0]

    # Pairwise latitudal separations between reference points
    y_21 = pt_2[:, 1] - pt_1[:, 1]
    y_31 = pt_3[:, 1] - pt_1[:, 1]
    y_42 = pt_4[:, 1] - pt_2[:, 1]

    a__ = x_31 * y_42 - y_31 * x_42
    b__ = out_y * (x_42 - x_31) - out_x * (y_42 - y_31) + \
        x_31 * pt_2[:, 1] - y_31 * pt_2[:, 0] + \
        pt_1[:, 0] * y_42 - pt_1[:, 1] * x_42
    c__ = out_y * x_21 - out_x * y_21 + pt_1[:, 0] * pt_2[:, 1] - \
        pt_2[:, 0] * pt_1[:, 1]

    # Get the valid roots from interval [0, 1]
    t__ = _solve_quadratic(a__, b__, c__, min_val=0., max_val=1.)

    # Calculate parameter s
    with np.errstate(divide='ignore'):
        s__ = ((out_y - pt_1[:, 1] - y_31 * t__) /
               (pt_2[:, 1] + y_42 * t__ - pt_1[:, 1] - y_31 * t__))

    # Limit also values of s to interval [0, 1]
    with np.errstate(invalid='ignore'):
        idxs = (s__ < 0) | (s__ > 1)
    s__[idxs] = np.nan

    return t__, s__


def _get_ts_uprights_parallel(pt_1, pt_2, pt_3, pt_4, out_y, out_x):
    """Get parameters for the case where uprights are parallel"""

    # Pairwise longitudal separations between reference points
    x_21 = pt_2[:, 0] - pt_1[:, 0]
    x_31 = pt_3[:, 0] - pt_1[:, 0]
    x_43 = pt_4[:, 0] - pt_3[:, 0]

    # Pairwise latitudal separations between reference points
    y_21 = pt_2[:, 1] - pt_1[:, 1]
    y_31 = pt_3[:, 1] - pt_1[:, 1]
    y_43 = pt_4[:, 1] - pt_3[:, 1]

    a__ = x_21 * y_43 - y_21 * x_43
    b__ = out_y * (x_43 - x_21) - out_x * (y_43 - y_21) + \
        pt_1[:, 0] * y_43 - pt_1[:, 1] * x_43 + \
        x_21 * pt_3[:, 1] - y_21 * pt_3[:, 0]
    c__ = out_y * x_31 - out_x * y_31 + \
        pt_1[:, 0] * pt_3[:, 1] - pt_3[:, 0] - pt_1[:, 0]

    s__ = _solve_quadratic(a__, b__, c__, min_val=0., max_val=1.)

    t__ = (out_y - pt_1[:, 1] - y_21 * s__) / \
          (pt_3[:, 1] + y_43 * s__ - pt_1[:, 1] - y_21 * s__)

    idxs = (t__ < 0.) | (t__ > 1.)
    t__[idxs] = np.nan

    return t__, s__


def _get_ts_parallellogram(pt_1, pt_2, pt_3, out_y, out_x):
    """Get parameters for the case where uprights are parallel"""

    # Pairwise longitudal separations between reference points
    x_21 = pt_2[:, 0] - pt_1[:, 0]
    x_31 = pt_3[:, 0] - pt_1[:, 0]

    # Pairwise latitudal separations between reference points
    y_21 = pt_2[:, 1] - pt_1[:, 1]
    y_31 = pt_3[:, 1] - pt_1[:, 1]

    t__ = (x_21 * (out_y - pt_1[:, 1]) - y_21 * (out_x - pt_1[:, 0])) / \
          (x_21 * y_31 - y_21 * x_31)
    idxs = (t__ < 0.) | (t__ > 1.)
    t__[idxs] = np.nan

    s__ = (out_x - pt_1[:, 0] + x_31 * t__) / x_21

    idxs = (s__ < 0.) | (s__ > 1.)
    s__[idxs] = np.nan

    return t__, s__


def _mask_coordinates(lons, lats):
    """Mask invalid coordinate values"""
    lons = lons.ravel()
    lats = lats.ravel()
    idxs = ((lons < -180.) | (lons > 180.) |
            (lats < -90.) | (lats > 90.))
    lons[idxs] = np.nan
    lats[idxs] = np.nan

    return lons, lats


def _get_corner(stride, valid, in_x, in_y, idx_ref):
    """Get closest set of coordinates from the *valid* locations"""
    idxs = np.argmax(valid, axis=1)
    invalid = np.invert(np.max(valid, axis=1))
    # Replace invalid points with np.nan
    x__ = in_x[stride, idxs]
    x__[invalid] = np.nan
    y__ = in_y[stride, idxs]
    y__[invalid] = np.nan
    idx = idx_ref[stride, idxs]

    return x__, y__, idx


def _get_bounding_corners(in_x, in_y, out_x, out_y, neighbours, idx_ref):
    """Get four closest locations from (in_x, in_y) so that they form a
    bounding rectangle around the requested location given by (out_x,
    out_y).
    """

    # Find four closest pixels around the target location

    # Tile output coordinates to same shape as neighbour info
    out_x_tile = np.tile(out_x, (neighbours, 1)).T
    out_y_tile = np.tile(out_y, (neighbours, 1)).T

    # Get differences in both directions
    x_diff = out_x_tile - in_x
    y_diff = out_y_tile - in_y

    stride = np.arange(x_diff.shape[0])

    # Upper left source pixel
    valid = (x_diff > 0) & (y_diff < 0)
    x_1, y_1, idx_1 = _get_corner(stride, valid, in_x, in_y, idx_ref)

    # Upper right source pixel
    valid = (x_diff < 0) & (y_diff < 0)
    x_2, y_2, idx_2 = _get_corner(stride, valid, in_x, in_y, idx_ref)

    # Lower left source pixel
    valid = (x_diff > 0) & (y_diff > 0)
    x_3, y_3, idx_3 = _get_corner(stride, valid, in_x, in_y, idx_ref)

    # Lower right source pixel
    valid = (x_diff < 0) & (y_diff > 0)
    x_4, y_4, idx_4 = _get_corner(stride, valid, in_x, in_y, idx_ref)

    # Combine sorted indices to idx_ref
    idx_ref = np.vstack((idx_1, idx_2, idx_3, idx_4)).T

    return (np.vstack((x_1, y_1)).T, np.vstack((x_2, y_2)).T,
            np.vstack((x_3, y_3)).T, np.vstack((x_4, y_4)).T, idx_ref)


def _solve_quadratic(a__, b__, c__, min_val=0.0, max_val=1.0):
    """Solve quadratic equation and return the valid roots from interval
    [*min_val*, *max_val*]

    """

    if not isinstance(a__, np.ndarray):
        if isinstance(a__, (int, float)):
            a__ = [a__]
        a__ = np.array(a__)
    if not isinstance(b__, np.ndarray):
        if isinstance(b__, (int, float)):
            b__ = [b__]
        b__ = np.array(b__)
    if not isinstance(c__, np.ndarray):
        if isinstance(c__, (int, float)):
            c__ = [c__]
        c__ = np.array(c__)

    discriminant = b__ * b__ - 4 * a__ * c__

    # Solve the quadratic polynomial
    with np.errstate(invalid='ignore'):
        x_1 = (-b__ + np.sqrt(discriminant)) / (2 * a__)
        x_2 = (-b__ - np.sqrt(discriminant)) / (2 * a__)

    # Find valid solutions, ie. 0 <= t <= 1
    x__ = x_1.copy()
    with np.errstate(invalid='ignore'):
        idxs = (x_1 < min_val) | (x_1 > max_val)
    x__[idxs] = x_2[idxs]

    with np.errstate(invalid='ignore'):
        idxs = (x__ < min_val) | (x__ > max_val)
    x__[idxs] = np.nan
    # x__ = np.ma.masked_where(idxs, t__)

    return x__


def _get_output_xy(out_area, proj):
    """Get x/y coordinates of the target grid."""
    # Read output coordinates
    out_lons, out_lats = out_area.get_lonlats()
    out_lons, out_lats = _mask_coordinates(out_lons, out_lats)

    out_x, out_y = proj(out_lons, out_lats)

    return out_x, out_y


def _get_input_xy(in_area, proj, input_idxs, idx_ref):
    """Get x/y coordinates for the input area and reduce the data."""
    in_lons, in_lats = in_area.get_lonlats()

    # Select valid locations
    in_lons = in_lons.ravel()[input_idxs]
    in_lats = in_lats.ravel()[input_idxs]

    # Mask invalid values
    in_lons, in_lats = _mask_coordinates(in_lons, in_lats)

    # Expand input coordinates for each output location
    in_lons = in_lons[idx_ref]
    in_lats = in_lats[idx_ref]

    # Convert coordinates to output projection x/y space
    in_x, in_y = proj(in_lons, in_lats)

    return in_x, in_y

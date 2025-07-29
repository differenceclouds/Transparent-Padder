#!/usr/bin/env python3
"""
Alpha Padder Command Line Tool
Pads transparent pixels in images by averaging RGB values from nearby opaque pixels.
Source: https://github.com/IRCSS/Transparent-Padder
DISCLOSURE: converted to command-line utility with Claude Sonnet 4
"""

import os
import sys
import argparse
from PIL import Image
import numpy as np
from scipy.ndimage import generic_filter, gaussian_filter, distance_transform_edt


def smooth_pad(data, mask, radius=3):
    """Apply smooth padding using neighborhood averaging."""
    weight_mask = mask.astype(np.float32)
    output = data.copy()
    for c in range(3):
        channel = data[:, :, c].astype(np.float32)
        sum_c = generic_filter(channel * weight_mask, np.sum, size=2*radius+1, mode='constant', cval=0.0)
        sum_w = generic_filter(weight_mask, np.sum, size=2*radius+1, mode='constant', cval=0.0)
        avg_c = np.divide(sum_c, sum_w, out=np.zeros_like(sum_c), where=sum_w > 0)
        output[:, :, c][~mask] = avg_c[~mask]
    return output.astype(np.uint8)


def flood_fill_pad(data, mask):
    """Fill remaining transparent areas using distance transform."""
    inverse_mask = ~mask
    if not np.any(inverse_mask):
        return data
    h, w = inverse_mask.shape
    result = data.copy()
    indices = np.indices((h, w))
    dist, (i_idx, j_idx) = distance_transform_edt(inverse_mask, return_indices=True)
    for c in range(3):
        result[:, :, c][inverse_mask] = data[i_idx, j_idx, c][inverse_mask]
    return result


def auto_set_params(image_size):
    """Auto-set parameters based on image resolution."""
    w, h = image_size
    max_dim = max(w, h)
    radius = max(2, int(max_dim / 512 * 3))
    sigma = round(min(10.0, max(1.0, max_dim / 1024 * 3)), 1)
    return radius, sigma


def load_mask_image(mask_path, expected_size):
    """Load and validate UV island mask."""
    try:
        mask_img = Image.open(mask_path).convert("L")
        if mask_img.size != expected_size:
            raise ValueError(f"Mask size {mask_img.size} doesn't match image size {expected_size}")
        return np.array(mask_img) > 128
    except Exception as e:
        print(f"Error loading mask: {e}", file=sys.stderr)
        sys.exit(1)


def pad_image(input_path, output_path=None, radius=None, blur_sigma=None, 
              uv_mask_path=None, auto_params=False, verbose=False):
    """Main padding function."""
    
    # Load input image
    try:
        orig_img = Image.open(input_path).convert("RGBA")
        if verbose:
            print(f"Loaded image: {input_path} ({orig_img.size[0]}x{orig_img.size[1]})")
    except Exception as e:
        print(f"Error loading image: {e}", file=sys.stderr)
        sys.exit(1)
    
    data = np.array(orig_img)
    h, w = data.shape[:2]
    
    # Auto-set parameters if requested
    if auto_params or (radius is None and blur_sigma is None):
        auto_radius, auto_sigma = auto_set_params(orig_img.size)
        if radius is None:
            radius = auto_radius
        if blur_sigma is None:
            blur_sigma = auto_sigma
        if verbose:
            print(f"Auto-set parameters: radius={radius}, blur_sigma={blur_sigma}")
    
    # Set defaults if still None
    if radius is None:
        radius = 3
    if blur_sigma is None:
        blur_sigma = 3.0
    
    # Determine mask based on UV mode or alpha channel
    use_uv_mask = uv_mask_path is not None
    
    if use_uv_mask:
        mask = load_mask_image(uv_mask_path, orig_img.size)
        if verbose:
            print(f"Using UV island mask from: {uv_mask_path}")
    else:
        alpha = data[:, :, 3]
        if np.all(alpha == 255):
            print("Image has no transparency. Nothing to pad.")
            return
        mask = alpha == 255
        # Zero out RGB values in transparent areas
        data[alpha < 255, :3] = 0
        if verbose:
            transparent_pixels = np.sum(~mask)
            print(f"Found {transparent_pixels} transparent pixels to pad")
    
    # Apply padding
    if verbose:
        print("Applying smooth padding...")
    padded_data = smooth_pad(data, mask, radius=radius)
    
    if verbose:
        print("Applying flood fill padding...")
    padded_data = flood_fill_pad(padded_data, mask)
    
    # Handle alpha channel
    if not use_uv_mask:
        if verbose:
            print(f"Applying alpha blur with sigma={blur_sigma}...")
        sigma = blur_sigma
        alpha = data[:, :, 3].astype(np.float32)
        fade_mask = (alpha < 255).astype(np.float32)
        distance = gaussian_filter(1 - fade_mask, sigma=sigma)
        normalized = np.clip((distance - distance.min()) / (distance.max() - distance.min()), 0, 1)
        faded_alpha = np.maximum(alpha, normalized * 255).astype(np.uint8)
        padded_data[:, :, 3] = faded_alpha
    else:
        # For UV mask mode, set alpha to fully opaque
        padded_data = np.concatenate([padded_data[:, :, :3], np.full((h, w, 1), 255, dtype=np.uint8)], axis=2)
    
    # Generate output path if not provided
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = base + "_padded.tga"
    
    # Save result
    try:
        result_img = Image.fromarray(padded_data, mode="RGBA")
        result_img.save(output_path)
        if verbose:
            print(f"Padded image saved to: {output_path}")
        else:
            print(output_path)
    except Exception as e:
        print(f"Error saving image: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Pad transparent pixels in images by averaging RGB values from nearby opaque pixels.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s image.png                          # Basic padding with auto parameters
  %(prog)s image.png -o output.tga            # Specify output file
  %(prog)s image.png -r 5 -s 2.0              # Custom radius and blur sigma
  %(prog)s texture.png -m mask.png            # Use UV island mask
  %(prog)s image.png --auto -v                # Auto parameters with verbose output
        """
    )
    
    parser.add_argument('input', help='Input image file (PNG, TGA, TIFF, BMP)')
    parser.add_argument('-o', '--output', help='Output file path (default: input_padded.tga)')
    parser.add_argument('-r', '--radius', type=int, help='RGB averaging radius (default: auto or 3)')
    parser.add_argument('-s', '--sigma', type=float, dest='blur_sigma', 
                       help='Alpha blur sigma for edge smoothing (default: auto or 3.0)')
    parser.add_argument('-m', '--mask', dest='uv_mask_path',
                       help='UV island mask image (enables UV texture mode)')
    parser.add_argument('--auto', action='store_true', 
                       help='Auto-set parameters based on image resolution')
    parser.add_argument('-v', '--verbose', action='store_true', 
                       help='Verbose output')
    
    args = parser.parse_args()
    
    # Validate input file exists
    if not os.path.exists(args.input):
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)
    
    # Validate mask file if provided
    if args.uv_mask_path and not os.path.exists(args.uv_mask_path):
        print(f"Error: Mask file '{args.uv_mask_path}' does not exist", file=sys.stderr)
        sys.exit(1)
    
    # Run the padding
    pad_image(
        input_path=args.input,
        output_path=args.output,
        radius=args.radius,
        blur_sigma=args.blur_sigma,
        uv_mask_path=args.uv_mask_path,
        auto_params=args.auto,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()
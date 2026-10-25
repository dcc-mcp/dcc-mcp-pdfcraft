"""Offline asset-contract oracles; these do not establish native host coverage."""

from __future__ import annotations

import importlib.util
import struct
import zlib
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/native_asset_handoff_acceptance.py"
SPEC = importlib.util.spec_from_file_location("native_asset_handoff_acceptance", SCRIPT)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)
REJECTED = ValueError


def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def png(*, width=2, height=2, depth=8, color=2, extra=b""):
    channels = 4 if color == 6 else 3
    rows = bytes([0] + [32, 64, 128, 255][:channels] * max(width, 0)) * max(height, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, 0))
        + extra
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("color", [2, 6])
def test_original_rgb_and_rgba_png_are_admitted(color):
    metadata = contract.inspect_png(png(color=color))
    assert metadata["width"] == metadata["height"] == 2


@pytest.mark.parametrize("tag", [b"sRGB", b"gAMA", b"iCCP", b"cHRM", b"cICP", b"tRNS"])
def test_tagged_or_color_key_png_requires_a_distinct_contract(tag):
    with pytest.raises(REJECTED):
        contract.inspect_png(png(extra=chunk(tag, b"\0")))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: b"not PNG",
        lambda data: data[:-1],
        lambda data: data + b"trailing bytes",
        lambda data: data[:20] + bytes([data[20] ^ 1]) + data[21:],
    ],
)
def test_corrupt_container_is_rejected_before_decoder_or_host_use(mutation):
    with pytest.raises(REJECTED):
        contract.inspect_png(mutation(png()))


@pytest.mark.parametrize(
    "options",
    [
        {"width": 0},
        {"height": 0},
        {"width": 2001},
        {"height": 2001},
        {"depth": 16},
        {"color": 0},
        {"color": 3},
        {"color": 4},
    ],
)
def test_unqualified_dimensions_or_pixel_modes_are_rejected(options):
    with pytest.raises(REJECTED):
        contract.inspect_png(png(**options))


def page(**changes):
    return {"/MediaBox": [0, 0, 72, 48], **changes}


def test_pdf_geometry_accepts_only_identity_page_mapping_with_default_box_values():
    contract.validate_geometry(page(), 72, 48)
    contract.validate_geometry(page(**{"/CropBox": [0, 0, 72, 48], "/Rotate": 0, "/UserUnit": 1}), 72, 48)
    contract.validate_geometry(page(**{"/MediaBox": [Decimal(0), Decimal(0), Decimal(72), Decimal(48)]}), 72, 48)


@pytest.mark.parametrize(
    "changes",
    [
        {"/MediaBox": [1, 0, 73, 48]},
        {"/MediaBox": [0, 0, 73, 48]},
        {"/MediaBox": [0, 0, 72, 47]},
        {"/CropBox": [0, 0, 71, 48]},
        {"/Rotate": 90},
        {"/UserUnit": 2},
        {"/MediaBox": [0, 0, float("nan"), 48]},
        {"/UserUnit": float("inf")},
        {"/MediaBox": [False, 0, 72, 48]},
        {"/UserUnit": "1"},
    ],
)
def test_pdf_geometry_drift_cannot_pass_by_render_size_alone(changes):
    with pytest.raises(REJECTED):
        contract.validate_geometry(page(**changes), 72, 48)


class ImageStream(dict):
    """Minimal pypdf-like decoded stream, with no native/PDF library dependency."""

    def __init__(self, data, **entries):
        super().__init__(entries)
        self.data = data

    def get_data(self):
        return self.data


class FakeBoolean:
    """PDF boolean objects are truthy Python objects even when their value is false."""

    def __init__(self, value):
        self.value = value


RGB = bytes((12, 34, 56, 78, 90, 123))


def expected(alpha=None):
    return {"width": 2, "height": 1, "rgb": RGB, "alpha": alpha}


def rgb_image(data=RGB, **entries):
    return ImageStream(
        data,
        **{
            "/Subtype": "/Image",
            "/Width": 2,
            "/Height": 1,
            "/BitsPerComponent": 8,
            "/ColorSpace": "/DeviceRGB",
            **entries,
        },
    )


def gray_mask(alpha=b"\x00\x80", **entries):
    return ImageStream(
        alpha,
        **{
            "/Subtype": "/Image",
            "/Width": 2,
            "/Height": 1,
            "/BitsPerComponent": 8,
            "/ColorSpace": "/DeviceGray",
            **entries,
        },
    )


def test_embedded_rgb_pixels_require_exact_decoded_input_bytes():
    contract.validate_image(rgb_image(), expected())
    contract.validate_image(rgb_image(**{"/Decode": [0, 1] * 3}), expected())
    for changed in (RGB[:-1], RGB + b"\0", bytes([RGB[0] ^ 1]) + RGB[1:]):
        with pytest.raises(REJECTED):
            contract.validate_image(rgb_image(changed), expected())


@pytest.mark.parametrize(
    "changes",
    [
        {"/Width": 3},
        {"/Height": 2},
        {"/BitsPerComponent": 16},
        {"/ColorSpace": "/DeviceGray"},
        {"/ColorSpace": ["/ICCBased", {}]},
        {"/ColorSpace": ["/CalRGB", {}]},
        {"/ColorSpace": ["/Indexed", "/DeviceRGB", 255, b"palette"]},
    ],
)
def test_implicit_color_or_geometry_conversion_is_not_byte_preservation(changes):
    with pytest.raises(REJECTED):
        contract.validate_image(rgb_image(**changes), expected())


@pytest.mark.parametrize(
    "changes", [{"/Decode": [1, 0] * 3}, {"/Mask": [12, 12, 34, 34, 56, 56]}, {"/ImageMask": True}]
)
def test_pdf_image_transforms_cannot_disguise_changed_appearance(changes):
    with pytest.raises(REJECTED):
        contract.validate_image(rgb_image(**changes), expected())


def test_nonopaque_input_requires_exact_straight_alpha_soft_mask():
    alpha = b"\x00\x80"
    contract.validate_image(rgb_image(**{"/SMask": gray_mask(alpha)}), expected(alpha))
    for mask in (None, gray_mask(b"\xff\x80"), gray_mask(b"\x00")):
        image = rgb_image() if mask is None else rgb_image(**{"/SMask": mask})
        with pytest.raises(REJECTED):
            contract.validate_image(image, expected(alpha))


@pytest.mark.parametrize(
    "changes", [{"/Width": 1}, {"/Height": 2}, {"/BitsPerComponent": 16}, {"/ColorSpace": "/DeviceRGB"}]
)
def test_alpha_stream_must_have_matching_grayscale_geometry(changes):
    with pytest.raises(REJECTED):
        contract.validate_image(rgb_image(**{"/SMask": gray_mask(**changes)}), expected(b"\x00\x80"))


@pytest.mark.parametrize("changes", [{"/Decode": [1, 0]}, {"/Matte": [1, 1, 1]}])
def test_soft_mask_decode_and_matte_are_not_silently_ignored(changes):
    with pytest.raises(REJECTED):
        contract.validate_image(rgb_image(**{"/SMask": gray_mask(**changes)}), expected(b"\x00\x80"))


@pytest.mark.parametrize("false_flag", [False, FakeBoolean(False)])
def test_explicit_false_pdf_flags_preserve_the_noninterpolated_image_contract(false_flag):
    flags = {"/Interpolate": false_flag, "/ImageMask": false_flag}
    image = rgb_image(**{**flags, "/SMask": gray_mask(**flags)})
    contract.validate_image(image, expected(b"\x00\x80"))


@pytest.mark.parametrize("target", ["image", "soft_mask"])
@pytest.mark.parametrize("true_flag", [True, FakeBoolean(True)])
def test_interpolation_in_either_plane_requires_a_different_sampling_contract(target, true_flag):
    image_flags = {"/Interpolate": true_flag} if target == "image" else {}
    mask_flags = {"/Interpolate": true_flag} if target == "soft_mask" else {}
    image = rgb_image(**{**image_flags, "/SMask": gray_mask(**mask_flags)})
    with pytest.raises(REJECTED):
        contract.validate_image(image, expected(b"\x00\x80"))


@pytest.mark.parametrize("target", ["image", "soft_mask"])
def test_pdf_boolean_wrapper_cannot_hide_an_enabled_image_mask(target):
    image_flags = {"/ImageMask": FakeBoolean(True)} if target == "image" else {}
    mask_flags = {"/ImageMask": FakeBoolean(True)} if target == "soft_mask" else {}
    image = rgb_image(**{**image_flags, "/SMask": gray_mask(**mask_flags)})
    with pytest.raises(REJECTED):
        contract.validate_image(image, expected(b"\x00\x80"))


def test_fixed_cell_center_sampling_reads_every_channel_including_boundary_cells():
    # Each rendered pixel identifies its own coordinate. This distinguishes the
    # declared (2, 2) offset from averaging, corners, neighboring cells or flips.
    rendered = bytes(channel for y in range(12) for x in range(12) for channel in (x, y, x + y, 255 - x - y))
    centers = bytes(
        (
            2,
            2,
            4,
            251,
            6,
            2,
            8,
            247,
            10,
            2,
            12,
            243,
            2,
            6,
            8,
            247,
            6,
            6,
            12,
            243,
            10,
            6,
            16,
            239,
            2,
            10,
            12,
            243,
            6,
            10,
            16,
            239,
            10,
            10,
            20,
            235,
        )
    )
    assert contract.sample_cell_centers(rendered, 3, 3) == centers
    assert contract.sample_cell_centers(rendered, 3, 3, scale=4) == centers


@pytest.mark.parametrize("length", [0, 60, 63, 65, 68])
def test_cell_center_sampling_rejects_truncated_or_extra_render_bytes(length):
    with pytest.raises(REJECTED):
        contract.sample_cell_centers(bytes(length), 1, 1)


@pytest.mark.parametrize(
    ("width", "height"),
    [(bad, 1) for bad in (0, -1, True, False, 1.0, "1", None)]
    + [(1, bad) for bad in (0, -1, True, False, 1.0, "1", None)],
)
def test_cell_center_sampling_requires_positive_integer_source_dimensions(width, height):
    with pytest.raises(REJECTED):
        contract.sample_cell_centers(bytes(64), width, height)


@pytest.mark.parametrize(
    ("scale", "length"),
    [(False, 0), (True, 4), (0, 0), (-4, 64), (1, 4), (2, 16), (3, 36), (5, 100), (4.0, 64), ("4", 64), (None, 64)],
)
def test_cell_center_sampling_scale_is_fixed_instead_of_inferred_from_the_render(scale, length):
    with pytest.raises(REJECTED):
        contract.sample_cell_centers(bytes(length), 1, 1, scale=scale)


def test_preview_compares_opaque_pdf_render_to_white_composited_source():
    source = bytes((10, 20, 30, 0, 12, 34, 56, 255))
    preview = bytes((255, 255, 255, 255, 12, 34, 56, 255))
    contract.compare_preview(preview, source, 2, 1)


@pytest.mark.parametrize(
    "preview",
    [
        bytes((0, 0, 0, 255, 12, 34, 56, 255)),
        bytes((255, 255, 255, 0, 12, 34, 56, 255)),
        bytes((255, 255, 255, 255, 12, 34, 56)),
    ],
)
def test_black_matte_lost_opacity_or_truncated_render_is_rejected(preview):
    source = bytes((10, 20, 30, 0, 12, 34, 56, 255))
    with pytest.raises(REJECTED):
        contract.compare_preview(preview, source, 2, 1)


def test_preview_mae_tolerance_is_applied_without_silent_widening():
    source = bytes((40, 80, 120, 255))
    contract.compare_preview(bytes((42, 82, 122, 255)), source, 1, 1, max_mae=2.0)
    with pytest.raises(REJECTED):
        contract.compare_preview(bytes((43, 83, 123, 255)), source, 1, 1, max_mae=2.0)

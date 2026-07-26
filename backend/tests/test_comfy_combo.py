"""combo_options must understand every /object_info combo shape we know of."""

import pytest

from app.comfy import ComfyError, combo_options


def _info(spec):
    return {"ResolutionSelector": {"input": {"required": {"aspect_ratio": spec}}}}


def test_classic_list_shape():
    info = _info([["1:1", "4:3 (Standard)"], {"default": "1:1"}])
    assert combo_options(info, "ResolutionSelector", "aspect_ratio") == [
        "1:1",
        "4:3 (Standard)",
    ]


def test_dict_shape():
    info = _info([{"options": ["a", "b"]}, {}])
    assert combo_options(info, "ResolutionSelector", "aspect_ratio") == ["a", "b"]


def test_v3_cloud_combo_shape():
    info = _info(["COMBO", {"options": ["16:9", "9:16"], "default": "16:9"}])
    assert combo_options(info, "ResolutionSelector", "aspect_ratio") == ["16:9", "9:16"]


def test_v3_cloud_values_key():
    info = _info(["COMBO", {"values": ["x"]}])
    assert combo_options(info, "ResolutionSelector", "aspect_ratio") == ["x"]


def test_empty_option_list_is_valid():
    info = _info([[], {}])
    assert combo_options(info, "ResolutionSelector", "aspect_ratio") == []


def test_non_combo_raises_with_spec_in_message():
    info = _info(["FLOAT", {"default": 1.0}])
    with pytest.raises(ComfyError, match="FLOAT"):
        combo_options(info, "ResolutionSelector", "aspect_ratio")


def test_missing_field_raises():
    info = {"ResolutionSelector": {"input": {"required": {}}}}
    with pytest.raises(ComfyError, match="has no options"):
        combo_options(info, "ResolutionSelector", "aspect_ratio")


def test_missing_class_raises():
    with pytest.raises(ComfyError, match="not available"):
        combo_options({}, "ResolutionSelector", "aspect_ratio")

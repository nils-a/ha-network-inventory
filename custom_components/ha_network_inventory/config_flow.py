"""Config and options flow for Network Inventory."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.selector import (
    BooleanSelector,
    LabelSelector,
    LabelSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    SEGMENT_LABEL,
    SEGMENT_RANGE,
    CONF_SEGMENTS,
    CONF_CREATE_DEFAULTS,
    CONF_DHCP,
    CONF_NETWORK,
    CONF_PLATFORMS,
    DEFAULT_SEGMENTS,
    DEFAULT_DHCP,
    DEFAULT_NETWORK,
    DEFAULT_PLATFORMS,
    DOMAIN,
)
from .logic import Segment, IpRange, validate_layout

STEP_SEGMENT = "segment"
FIELD_DELETE = "delete"


def _parse(text: str | None) -> IpRange | None:
    """Parse a range; raise ValueError on bad input, None for empty."""
    if not text or not text.strip():
        return None
    return IpRange.parse(text)


def _check(options: dict[str, Any]) -> str | None:
    """Validate a complete options dict, return an error key or None."""
    try:
        network = IpRange.parse(options[CONF_NETWORK])
        dhcp = _parse(options.get(CONF_DHCP))
        segments = [
            Segment(c[SEGMENT_LABEL], IpRange.parse(c[SEGMENT_RANGE]))
            for c in options.get(CONF_SEGMENTS, [])
        ]
    except ValueError:
        return "invalid_range"
    return validate_layout(network, segments, dhcp)


def _ensure_label(hass: HomeAssistant, name: str, icon: str, color: str) -> str:
    registry = lr.async_get(hass)
    label = registry.async_get_label_by_name(name)
    if label is None:
        label = registry.async_create(name, icon=icon, color=color)
    return label.label_id


class NetworkInventoryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            segments: list[dict[str, str]] = []
            if user_input.get(CONF_CREATE_DEFAULTS):
                segments = [
                    {SEGMENT_LABEL: name, SEGMENT_RANGE: rng}
                    for name, rng, _icon, _color in DEFAULT_SEGMENTS
                ]
            options = {
                CONF_NETWORK: user_input[CONF_NETWORK],
                CONF_DHCP: user_input.get(CONF_DHCP, ""),
                CONF_PLATFORMS: DEFAULT_PLATFORMS,
                CONF_SEGMENTS: segments,
            }
            # Validate with placeholder label ids before creating labels.
            if (error := _check(options)) is not None:
                errors["base"] = error
            else:
                lookup = {name: (icon, color) for name, _rng, icon, color in DEFAULT_SEGMENTS}
                for segment in segments:
                    icon, color = lookup[segment[SEGMENT_LABEL]]
                    segment[SEGMENT_LABEL] = _ensure_label(
                        self.hass, segment[SEGMENT_LABEL], icon, color
                    )
                return self.async_create_entry(
                    title="Network Inventory", data={}, options=options
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NETWORK, default=DEFAULT_NETWORK): TextSelector(),
                vol.Optional(CONF_DHCP, default=DEFAULT_DHCP): TextSelector(),
                vol.Required(CONF_CREATE_DEFAULTS, default=True): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NetworkInventoryOptionsFlow()


class NetworkInventoryOptionsFlow(OptionsFlow):
    """Edit segments, DHCP range and general settings."""

    def __init__(self) -> None:
        self._edit_label: str | None = None

    def _options(self) -> dict[str, Any]:
        opts = dict(self.config_entry.options)
        opts[CONF_SEGMENTS] = [dict(c) for c in opts.get(CONF_SEGMENTS, [])]
        return opts

    def _label_name(self, label_id: str) -> str:
        label = lr.async_get(self.hass).async_get_label(label_id)
        return label.name if label else label_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["add_segment"]
        if self.config_entry.options.get(CONF_SEGMENTS):
            menu.append("edit_segment")
        menu.append("general")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        opts = self._options()
        errors: dict[str, str] = {}
        if user_input is not None:
            opts[CONF_NETWORK] = user_input[CONF_NETWORK]
            opts[CONF_DHCP] = user_input.get(CONF_DHCP, "")
            opts[CONF_PLATFORMS] = user_input.get(CONF_PLATFORMS) or DEFAULT_PLATFORMS
            if (error := _check(opts)) is None:
                return self.async_create_entry(data=opts)
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_NETWORK, default=opts.get(CONF_NETWORK, DEFAULT_NETWORK)): TextSelector(),
                vol.Optional(
                    CONF_DHCP, description={"suggested_value": opts.get(CONF_DHCP, "")}
                ): TextSelector(),
                vol.Required(
                    CONF_PLATFORMS, default=opts.get(CONF_PLATFORMS, DEFAULT_PLATFORMS)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=["fritz"], multiple=True, custom_value=True
                    )
                ),
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema, errors=errors)

    async def async_step_add_segment(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        opts = self._options()
        errors: dict[str, str] = {}
        if user_input is not None:
            label_id = user_input[SEGMENT_LABEL]
            if any(c[SEGMENT_LABEL] == label_id for c in opts[CONF_SEGMENTS]):
                errors["base"] = "label_in_use"
            else:
                opts[CONF_SEGMENTS].append(
                    {SEGMENT_LABEL: label_id, SEGMENT_RANGE: user_input[SEGMENT_RANGE]}
                )
                if (error := _check(opts)) is None:
                    return self.async_create_entry(data=opts)
                errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(SEGMENT_LABEL): LabelSelector(LabelSelectorConfig()),
                vol.Required(SEGMENT_RANGE): TextSelector(),
            }
        )
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(step_id="add_segment", data_schema=schema, errors=errors)

    async def async_step_edit_segment(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        opts = self._options()
        if user_input is not None:
            self._edit_label = user_input[STEP_SEGMENT]
            return await self.async_step_edit_segment_details()

        choices = [
            SelectOptionDict(
                value=c[SEGMENT_LABEL],
                label=f"{self._label_name(c[SEGMENT_LABEL])} ({c[SEGMENT_RANGE]})",
            )
            for c in opts[CONF_SEGMENTS]
        ]
        schema = vol.Schema(
            {
                vol.Required(STEP_SEGMENT): SelectSelector(
                    SelectSelectorConfig(options=choices, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(step_id="edit_segment", data_schema=schema)

    async def async_step_edit_segment_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        opts = self._options()
        current = next(
            (c for c in opts[CONF_SEGMENTS] if c[SEGMENT_LABEL] == self._edit_label), None
        )
        if current is None:
            return self.async_abort(reason="segment_not_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(FIELD_DELETE):
                opts[CONF_SEGMENTS] = [
                    c for c in opts[CONF_SEGMENTS] if c[SEGMENT_LABEL] != self._edit_label
                ]
                return self.async_create_entry(data=opts)
            current[SEGMENT_RANGE] = user_input[SEGMENT_RANGE]
            if (error := _check(opts)) is None:
                return self.async_create_entry(data=opts)
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(SEGMENT_RANGE, default=current[SEGMENT_RANGE]): TextSelector(),
                vol.Optional(FIELD_DELETE, default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="edit_segment_details",
            data_schema=schema,
            errors=errors,
            description_placeholders={"label": self._label_name(current[SEGMENT_LABEL])},
        )

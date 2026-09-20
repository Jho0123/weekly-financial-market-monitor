"""Optional TradingView Economic Calendar embed.

This is the official widget, loaded in an iframe. It is a visual
cross-check only and never feeds the database: nothing on this page is
scraped, and the macro table above is generated entirely from official
agency calendars (spec sections 31 and 66).
"""

import json

import streamlit as st
import streamlit.components.v1 as components

WIDGET_SRC = (
    "https://s3.tradingview.com/external-embedding/"
    "embed-widget-events.js"
)


def _html(height: int, config: dict) -> str:
    return """
<div class="tradingview-widget-container">
  <div class="tradingview-widget-container__widget"></div>
  <script type="text/javascript" src="{src}" async>
  {config}
  </script>
</div>
""".format(src=WIDGET_SRC, config=json.dumps(config))


def render(timezone: str = "America/Toronto", height: int = 600) -> None:
    st.subheader("TradingView economic calendar")
    st.caption(
        "Official TradingView widget, shown for visual cross-checking only. "
        "The table above is the source of truth and comes from BLS, BEA, "
        "the Federal Reserve and the Census Bureau."
    )

    config = {
        "colorTheme": "light",
        "isTransparent": False,
        "width": "100%",
        "height": height,
        "locale": "en",
        "importanceFilter": "0,1",
        "countryFilter": "us",
        "timezone": timezone,
    }

    components.html(_html(height, config), height=height + 20, scrolling=True)

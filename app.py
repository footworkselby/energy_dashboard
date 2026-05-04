import os
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from influxdb import InfluxDBClient
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration — edit these if your setup differs
# ---------------------------------------------------------------------------

INFLUX_HOST = os.getenv("INFLUX_HOST", "homeassistant.local")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB   = os.getenv("INFLUX_DB", "evohome")

TEMP_SENSORS = {
    "snsalfie_temperature":         "Alfie",
    "snshenry_temperature":         "Henry",
    "snskitchenhall_temperature":   "Kitchen / Hall",
    "snslivingroom_temperature":    "Living Room",
    "snsmarkandhannah_temperature": "Mark & Hannah",
    "snspaincave_temperature":      "Pain Cave",
    "ewelink_snzb_02p_temperature": "External (eWeLink)",
    "h5075_07da_temperature":       "H5075 A",
    "h5075_8108_temperature":       "H5075 B",
    "h5075_d81d_temperature":       "H5075 C",
    "sonoff_snzb_02ld":             "Sonoff",
}

POWER_ENTITY = os.getenv("POWER_ENTITY", "")
POWER_SENSORS = {
    POWER_ENTITY: "Grid Demand",
}

ELECTRICITY_ENTITY = os.getenv("ELECTRICITY_ENTITY", "")
GAS_ENTITY = os.getenv("GAS_ENTITY", "")

# Colours
ELEC_COLOUR = "#4f9cf9"
GAS_COLOUR  = "#f97316"
PLOT_BG     = "rgba(0,0,0,0)"

# ---------------------------------------------------------------------------
# InfluxDB helpers
# ---------------------------------------------------------------------------

@st.cache_resource
def get_client():
    return InfluxDBClient(host=INFLUX_HOST, port=INFLUX_PORT, database=INFLUX_DB)


def run_query(q: str):
    try:
        return get_client().query(q)
    except Exception as e:
        st.error(f"Query failed: {e}")
        return None


def fetch_energy(entity_id: str, days: int) -> pd.DataFrame:
    q = f"""
        SELECT mean("value") AS value
        FROM "kWh"
        WHERE "entity_id" = '{entity_id}'
          AND time > now() - {days}d
        GROUP BY time(1h) fill(none)
    """
    result = run_query(q)
    if not result:
        return pd.DataFrame()
    points = list(result.get_points())
    if not points:
        return pd.DataFrame()
    df = pd.DataFrame(points)
    df["time"] = pd.to_datetime(df["time"])
    return df


def fetch_temperatures(days: int) -> pd.DataFrame:
    q = f"""
        SELECT mean("value") AS value
        FROM "°C"
        WHERE time > now() - {days}d
        GROUP BY time(15m), "entity_id" fill(none)
    """
    result = run_query(q)
    if not result:
        return pd.DataFrame()
    frames = []
    for (_, tags), points_gen in result.items():
        entity_id = tags.get("entity_id", "")
        if entity_id not in TEMP_SENSORS:
            continue
        points = list(points_gen)
        if not points:
            continue
        df = pd.DataFrame(points)
        df["time"] = pd.to_datetime(df["time"])
        df["room"] = TEMP_SENSORS[entity_id]
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_live_power(days: int) -> pd.DataFrame:
    q = f"""
        SELECT mean("value") AS value
        FROM "W"
        WHERE time > now() - {days}d
        GROUP BY time(5m), "entity_id" fill(none)
    """
    result = run_query(q)
    if not result:
        return pd.DataFrame()
    frames = []
    for (_, tags), points_gen in result.items():
        entity_id = tags.get("entity_id", "")
        points = list(points_gen)
        if not points:
            continue
        df = pd.DataFrame(points)
        df["time"] = pd.to_datetime(df["time"])
        df["entity_id"] = entity_id
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# Energy processing: accumulative → daily totals
# ---------------------------------------------------------------------------

def daily_from_accumulative(df: pd.DataFrame) -> pd.DataFrame:
    """
    Octopus 'current_accumulative_consumption' resets each day.
    The max value each day is therefore that day's total consumption.
    """
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["date"] = df["time"].dt.date
    daily = df.groupby("date")["value"].max().reset_index()
    daily.columns = ["date", "kwh"]
    daily = daily[daily["kwh"] > 0].reset_index(drop=True)
    return daily


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Home Energy Dashboard",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ Home Energy Dashboard")
st.caption(f"InfluxDB · {INFLUX_HOST}:{INFLUX_PORT} · database: {INFLUX_DB}")

tab_energy, tab_temps, tab_power = st.tabs(["Energy", "Temperatures", "Live Power"])


# ---------------------------------------------------------------------------
# Tab 1 — Energy
# ---------------------------------------------------------------------------

with tab_energy:
    col_spacer, col_ctrl = st.columns([4, 1])
    with col_ctrl:
        days = st.selectbox(
            "Time range",
            [7, 14, 30, 90],
            index=2,
            format_func=lambda x: f"Last {x} days",
            key="energy_days",
        )

    elec_df  = fetch_energy(ELECTRICITY_ENTITY, days)
    gas_df   = fetch_energy(GAS_ENTITY, days)
    daily_el = daily_from_accumulative(elec_df)
    daily_gas = daily_from_accumulative(gas_df)

    # --- Summary metrics ---
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        "Avg daily electricity",
        f"{daily_el['kwh'].mean():.1f} kWh" if not daily_el.empty else "—",
    )
    m2.metric(
        "Total electricity",
        f"{daily_el['kwh'].sum():.0f} kWh" if not daily_el.empty else "—",
    )
    m3.metric(
        "Avg daily gas",
        f"{daily_gas['kwh'].mean():.1f} kWh" if not daily_gas.empty else "—",
    )
    m4.metric(
        "Total gas",
        f"{daily_gas['kwh'].sum():.0f} kWh" if not daily_gas.empty else "—",
    )

    st.divider()

    # --- Electricity chart ---
    if not daily_el.empty:
        fig_el = px.bar(
            daily_el,
            x="date",
            y="kwh",
            title="Daily Electricity Consumption",
            color_discrete_sequence=[ELEC_COLOUR],
        )
        fig_el.update_layout(
            xaxis_title="",
            yaxis_title="kWh",
            plot_bgcolor=PLOT_BG,
            paper_bgcolor=PLOT_BG,
            hovermode="x unified",
        )
        st.plotly_chart(fig_el, use_container_width=True)
    else:
        st.info("No electricity data returned. Check the entity ID or time range.")

    # --- Gas chart ---
    if not daily_gas.empty:
        fig_gas = px.bar(
            daily_gas,
            x="date",
            y="kwh",
            title="Daily Gas Consumption",
            color_discrete_sequence=[GAS_COLOUR],
        )
        fig_gas.update_layout(
            xaxis_title="",
            yaxis_title="kWh",
            plot_bgcolor=PLOT_BG,
            paper_bgcolor=PLOT_BG,
            hovermode="x unified",
        )
        st.plotly_chart(fig_gas, use_container_width=True)
    else:
        st.info("No gas data returned. Check the entity ID or time range.")

    # --- Combined comparison ---
    if not daily_el.empty and not daily_gas.empty:
        st.subheader("Electricity vs Gas")
        combined = pd.merge(
            daily_el.rename(columns={"kwh": "Electricity"}),
            daily_gas.rename(columns={"kwh": "Gas"}),
            on="date",
            how="outer",
        ).fillna(0)
        fig_comb = go.Figure()
        fig_comb.add_bar(x=combined["date"], y=combined["Electricity"],
                         name="Electricity", marker_color=ELEC_COLOUR)
        fig_comb.add_bar(x=combined["date"], y=combined["Gas"],
                         name="Gas", marker_color=GAS_COLOUR)
        fig_comb.update_layout(
            barmode="group",
            xaxis_title="",
            yaxis_title="kWh",
            plot_bgcolor=PLOT_BG,
            paper_bgcolor=PLOT_BG,
            hovermode="x unified",
        )
        st.plotly_chart(fig_comb, use_container_width=True)


# ---------------------------------------------------------------------------
# Tab 2 — Temperatures
# ---------------------------------------------------------------------------

with tab_temps:
    col_spacer, col_ctrl2 = st.columns([4, 1])
    with col_ctrl2:
        temp_days = st.selectbox(
            "Time range",
            [1, 3, 7, 14],
            index=2,
            format_func=lambda x: f"Last {x} days",
            key="temp_days",
        )

    temp_df = fetch_temperatures(temp_days)

    if not temp_df.empty:
        # Current readings
        st.subheader("Current Readings")
        latest = (
            temp_df.sort_values("time")
            .groupby("room")
            .last()
            .reset_index()[["room", "value"]]
            .sort_values("value", ascending=False)
        )
        cols = st.columns(min(len(latest), 6))
        for i, row in latest.iterrows():
            cols[i % len(cols)].metric(row["room"], f"{row['value']:.1f}°C")

        st.divider()

        # Line chart — all rooms
        st.subheader("Temperature History")

        rooms_available = sorted(temp_df["room"].unique())
        selected_rooms = st.multiselect(
            "Rooms to display",
            options=rooms_available,
            default=[r for r in rooms_available if r in [
                "Alfie", "Henry", "Kitchen / Hall", "Living Room",
                "Mark & Hannah", "Pain Cave"
            ]],
        )

        filtered = temp_df[temp_df["room"].isin(selected_rooms)] if selected_rooms else temp_df

        fig_temp = px.line(
            filtered,
            x="time",
            y="value",
            color="room",
            title="Room Temperatures",
            labels={"value": "°C", "time": "", "room": "Room"},
        )
        fig_temp.update_layout(
            plot_bgcolor=PLOT_BG,
            paper_bgcolor=PLOT_BG,
            hovermode="x unified",
            yaxis_title="°C",
        )
        st.plotly_chart(fig_temp, use_container_width=True)

        # Min/max summary table
        st.subheader("Range Summary")
        summary = (
            temp_df.groupby("room")["value"]
            .agg(["min", "mean", "max"])
            .round(1)
            .reset_index()
        )
        summary.columns = ["Room", "Min °C", "Avg °C", "Max °C"]
        st.dataframe(summary.sort_values("Avg °C", ascending=False),
                     use_container_width=True, hide_index=True)
    else:
        st.info("No temperature data returned.")


# ---------------------------------------------------------------------------
# Tab 3 — Live Power
# ---------------------------------------------------------------------------

with tab_power:
    col_spacer, col_ctrl3 = st.columns([4, 1])
    with col_ctrl3:
        power_days = st.selectbox(
            "Time range",
            [1, 3, 7],
            index=0,
            format_func=lambda x: f"Last {x} day{'s' if x > 1 else ''}",
            key="power_days",
        )

    power_df = fetch_live_power(power_days)

    if not power_df.empty:
        # Apply friendly labels
        power_df["label"] = power_df["entity_id"].map(POWER_SENSORS).fillna(power_df["entity_id"])

        latest_val = power_df.sort_values("time").iloc[-1]["value"]
        peak_val   = power_df["value"].max()
        avg_val    = power_df["value"].mean()

        p1, p2, p3 = st.columns(3)
        p1.metric("Current demand", f"{latest_val:.0f} W")
        p2.metric("Peak", f"{peak_val:.0f} W")
        p3.metric("Average", f"{avg_val:.0f} W")

        st.divider()

        fig_power = px.line(
            power_df,
            x="time",
            y="value",
            color="label",
            title="Grid Electricity Demand",
            labels={"value": "Watts", "time": "", "label": ""},
        )
        fig_power.update_layout(
            plot_bgcolor=PLOT_BG,
            paper_bgcolor=PLOT_BG,
            hovermode="x unified",
            showlegend=False,
        )
        fig_power.update_traces(line_color=ELEC_COLOUR)
        st.plotly_chart(fig_power, use_container_width=True)

        # Demand distribution
        st.subheader("Demand Distribution")
        fig_hist = px.histogram(
            power_df,
            x="value",
            nbins=40,
            title="How often is demand at each level?",
            labels={"value": "Watts", "count": "Frequency"},
            color_discrete_sequence=[ELEC_COLOUR],
        )
        fig_hist.update_layout(
            plot_bgcolor=PLOT_BG,
            paper_bgcolor=PLOT_BG,
            yaxis_title="Frequency",
        )
        st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.info("No power (W) data returned.")

import dash
from dash import dcc, html, Input, Output, State, dash_table
import plotly.graph_objects as GO
import sqlite3
import pandas as pd
from pathlib import Path

# Initialize App
app = dash.Dash(__name__, title="Nifty CAS")

# DB path
project_root = Path(__file__).resolve().parent.parent.parent
db_path = project_root / 'data' / 'db' / 'research.db'

def get_session_data():
    conn = sqlite3.connect(db_path)
    df = pd.read_sql('''
        SELECT timestamp, spot_price, strike, option_side, close 
        FROM raw_options_data 
        WHERE strike = 24600 
        ORDER BY timestamp
    ''', conn)
    conn.close()
    
    if df.empty:
        return df, df, df
        
    spot_df = df[['timestamp', 'spot_price']].drop_duplicates()
    
    ce_df = df[df['option_side'] == 'CE'][['timestamp', 'close']].rename(columns={'close': 'ce_price'})
    pe_df = df[df['option_side'] == 'PE'][['timestamp', 'close']].rename(columns={'close': 'pe_price'})
    
    merged = pd.merge(ce_df, pe_df, on='timestamp')
    merged['ratio'] = merged.apply(lambda row: max(row['ce_price'], row['pe_price']) / min(row['ce_price'], row['pe_price']) if min(row['ce_price'], row['pe_price']) > 0 else 1.0, axis=1)
    
    return spot_df, merged, df

def get_candidates():
    conn = sqlite3.connect(db_path)
    df = pd.read_sql('SELECT * FROM candidate_events', conn)
    conn.close()
    return df

def get_outcomes():
    conn = sqlite3.connect(db_path)
    df = pd.read_sql('SELECT * FROM forward_outcomes', conn)
    conn.close()
    return df

# Layout
app.layout = html.Div([
    # 1. SYNTHETIC TEST DATA banner
    html.Div(
        "SYNTHETIC TEST DATA", 
        style={
            'backgroundColor': '#ff4d4d', 'color': 'white', 'padding': '10px', 
            'textAlign': 'center', 'fontWeight': 'bold', 'fontSize': '20px',
            'position': 'sticky', 'top': 0, 'zIndex': 999
        }
    ),
    
    html.H1("Phase 0.25 Research Terminal", style={'textAlign': 'center'}),
    
    # 2. Session overview
    html.Div([
        html.H2("Session Overview"),
        dcc.Graph(id='session-chart')
    ], style={'padding': '20px'}),
    
    # 3. Candidate Events Table
    html.Div([
        html.H2("Candidate Events"),
        dash_table.DataTable(id='candidates-table', style_table={'overflowX': 'auto'})
    ], style={'padding': '20px'}),
    
    # 4. Outcomes Summary
    html.Div([
        html.H2("Outcome Summary"),
        dash_table.DataTable(id='outcomes-table', style_table={'overflowX': 'auto'})
    ], style={'padding': '20px'}),
    
    # 5. Bias logging panel
    html.Div([
        html.H2("Bias Logging Panel"),
        html.Div([
            html.Label("Date: "),
            dcc.Input(id='bias-date', type='text', value='2026-10-15'),
        ], style={'margin': '10px'}),
        html.Div([
            html.Label("Index: "),
            dcc.Dropdown(id='bias-index', options=[{'label': 'NIFTY', 'value': 'NIFTY'}], value='NIFTY', style={'width': '200px', 'display': 'inline-block'})
        ], style={'margin': '10px'}),
        html.Div([
            html.Label("Bias Direction: "),
            dcc.Dropdown(id='bias-dir', options=[
                {'label': 'BULLISH', 'value': 'BULLISH'},
                {'label': 'BEARISH', 'value': 'BEARISH'},
                {'label': 'NEUTRAL', 'value': 'NEUTRAL'}
            ], value='NEUTRAL', style={'width': '200px', 'display': 'inline-block'})
        ], style={'margin': '10px'}),
        html.Div([
            html.Label("Confidence (1-5): "),
            dcc.Slider(1, 5, 1, value=3, id='bias-conf')
        ], style={'margin': '10px'}),
        html.Div([
            html.Label("Factors: "),
            dcc.Checklist(id='bias-factors', options=[
                {'label': ' SMC', 'value': 'SMC'},
                {'label': ' OI', 'value': 'OI'},
                {'label': ' PROP', 'value': 'PROP'},
                {'label': ' OTHER', 'value': 'OTHER'}
            ], value=[])
        ], style={'margin': '10px'}),
        html.Div([
            html.Label("Override? "),
            dcc.Checklist(id='bias-override', options=[{'label': 'Yes', 'value': 1}], value=[])
        ], style={'margin': '10px'}),
        html.Button("Submit Bias", id='bias-submit', n_clicks=0, style={'margin': '10px', 'padding': '10px', 'fontSize': '16px'}),
        html.Div(id='bias-output', style={'margin': '10px', 'color': 'green'})
    ], style={'padding': '20px', 'border': '1px solid black', 'margin': '20px', 'backgroundColor': '#f9f9f9'})
])

@app.callback(
    Output('session-chart', 'figure'),
    Output('candidates-table', 'data'),
    Output('outcomes-table', 'data'),
    Input('session-chart', 'id')
)
def update_dashboard(_):
    spot_df, merged, raw_df = get_session_data()
    
    fig = GO.Figure()
    if not spot_df.empty:
        # We need subplots, but for simplicity, put them on secondary y axis
        from plotly.subplots import make_subplots
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(GO.Scatter(x=spot_df['timestamp'], y=spot_df['spot_price'], name="Spot"), secondary_y=False)
        if not merged.empty:
            fig.add_trace(GO.Scatter(x=merged['timestamp'], y=merged['ce_price'], name="CE 24600"), secondary_y=True)
            fig.add_trace(GO.Scatter(x=merged['timestamp'], y=merged['pe_price'], name="PE 24600"), secondary_y=True)
            fig.add_trace(GO.Scatter(x=merged['timestamp'], y=merged['ratio'], name="CE/PE Ratio", line=dict(dash='dot')), secondary_y=True)
            
    c_df = get_candidates()
    o_df = get_outcomes()
    
    return fig, c_df.to_dict('records'), o_df.to_dict('records')

@app.callback(
    Output('bias-output', 'children'),
    Input('bias-submit', 'n_clicks'),
    State('bias-date', 'value'),
    State('bias-index', 'value'),
    State('bias-dir', 'value'),
    State('bias-conf', 'value'),
    State('bias-factors', 'value'),
    State('bias-override', 'value')
)
def save_bias(n_clicks, date, index, bias_dir, conf, factors, override):
    if n_clicks > 0:
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO bias_log (log_date, expiry_date, index_name, bias_direction, confidence, factors_smc, factors_oi, factors_prop, factors_other, override_flag)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (date, date, index, bias_dir, conf, 
                 1 if 'SMC' in factors else 0,
                 1 if 'OI' in factors else 0,
                 1 if 'PROP' in factors else 0,
                 'OTHER' if 'OTHER' in factors else '',
                 1 if len(override) > 0 else 0))
            conn.commit()
            conn.close()
            return f"Bias saved successfully at {pd.Timestamp.now()}!"
        except Exception as e:
            return f"Error: {e}"
    return ""

if __name__ == '__main__':
    app.run_server(debug=True, port=8050)

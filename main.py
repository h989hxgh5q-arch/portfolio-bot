from flask import Flask, request
import gspread
from google.oauth2.service_account import Credentials
from twilio.rest import Client
import google.generativeai as genai
import os, json, re
from datetime import datetime
import requests

app = Flask(__name__)

TWILIO_SID   = os.environ['TWILIO_SID']
TWILIO_TOKEN = os.environ['TWILIO_TOKEN']
TWILIO_FROM  = os.environ['TWILIO_FROM']
GEMINI_KEY   = os.environ['GEMINI_KEY']
SHEET_ID     = os.environ['SHEET_ID']
SHEET_CREDS  = os.environ['SHEET_CREDS']

genai.configure(api_key=GEMINI_KEY)

TICKERS_CONOCIDOS = {
    'sp500': 'SXR8.DE', 's&p500': 'SXR8.DE', 's&p 500': 'SXR8.DE',
    'world': 'EUNL.DE', 'msci world': 'EUNL.DE',
    'emerging': 'IS3N.DE', 'em': 'IS3N.DE', 'emergentes': 'IS3N.DE',
    'bitcoin': 'BTC-USD', 'btc': 'BTC-USD',
    'ethereum': 'ETH-USD', 'eth': 'ETH-USD',
    'aave': 'AAVE-USD',
    'uniswap': 'UNI1-USD', 'uni': 'UNI1-USD',
    'yearn': 'YFI-USD', 'yfi': 'YFI-USD',
    'arbitrum': 'ARB11841-USD', 'arb': 'ARB11841-USD',
    'ypf': 'YPF', 'galicia': 'GGAL.BA', 'ggal': 'GGAL.BA',
    'mercadolibre': 'MELI.BA', 'meli': 'MELI.BA',
}

NOMBRES = {
    'SXR8.DE': 'iShares Core S&P 500 UCITS ETF',
    'EUNL.DE': 'iShares Core MSCI World UCITS ETF',
    'IS3N.DE': 'iShares Core MSCI EM IMI UCITS ETF',
    'SPIUSGKL.CO': 'Sparinvest INDEX USA Growth KL',
    'SPVIGAKL.CO': 'Sparinvest INDEX Glob Akt KL',
    'SPIEMIKL.CO': 'Sparinvest INDEX Emerging Mkts KL',
    'SPVIBGKL.CO': 'Sparindex INDEX Bæredygtige Global KL',
    'SPIEUVKL.CO': 'Sparinvest INDEX Europa Value KL',
    'BTC-USD': 'Bitcoin', 'ETH-USD': 'Ethereum', 'AAVE-USD': 'Aave',
    'UNI1-USD': 'Uniswap', 'YFI-USD': 'yearn.finance',
    'ARB11841-USD': 'Arbitrum', 'YPF': 'YPF S.A.',
    'GGAL.BA': 'Grupo Financiero Galicia', 'MELI.BA': 'MercadoLibre',
}

def get_sheet():
    creds_dict = json.loads(SHEET_CREDS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=[
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet('Transacciones')

def append_transaction(ticker, name, qty, price, currency, notes=''):
    ws = get_sheet()
    today = datetime.today().strftime('%Y-%m-%d')
    fx = {'EUR': 7.46, 'USD': 6.89, 'DKK': 1.0, 'ARS': 0.0046}
    rate = fx.get(currency.upper(), 1.0)
    cost_dkk = round(qty * price * rate, 0)
    row = [today, ticker.upper(), name, qty, price, currency.upper(), cost_dkk, notes]
    ws.append_row(row, value_input_option='USER_ENTERED')
    return cost_dkk

def parse_with_gemini(text):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = """Sos un asistente financiero. Extraé los datos de compra de esta transacción.

Devolvé SOLO un JSON válido con este formato exacto (sin markdown, sin explicación):
{"ticker": "SXRS.DE", "qty": 10, "price": 44.50, "currency": "EUR", "notes": ""}

Reglas:
- ticker: el símbolo exacto si lo dice, o el nombre del activo
- qty: cantidad de unidades compradas (número)
- price: precio por unidad (número)
- currency: EUR, USD, DKK o ARS
- notes: cualquier nota adicional que mencione

Si no podés extraer algún campo con certeza, ponelo como null.
Si el mensaje no es una transacción de compra, devolvé {"error": "no es una transacción"}

Mensaje a interpretar:
""" + text
    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
        raw = re.sub(r'```json|```', '', raw).strip()
        return json.loads(raw)
    except Exception as e:
        return {"error": str(e)}

def send_whatsapp(to, message):
    client = Client(TWILIO_SID, TWILIO_TOKEN)
    client.messages.create(body=message, from_=TWILIO_FROM, to=to)

@app.route('/webhook', methods=['POST'])
def webhook():
    from_number = request.form.get('From', '')
    body = request.form.get('Body', '').strip()

    if body.lower() in ['ayuda', 'help', 'hola', '?']:
        send_whatsapp(from_number,
            "💼 *Portfolio Bot*\n\n"
            "Podés enviarme:\n"
            "• Un mensaje: _compré 10 SXRS.DE a 44.50 EUR_\n"
            "• Formato corto: _SXRS.DE 10 44.50 EUR_\n\n"
            "Agrego la transacción a tu Google Sheet automáticamente ✅"
        )
        return 'OK', 200

    result = parse_with_gemini(body)

    if 'error' in result:
        send_whatsapp(from_number,
            "❌ No pude interpretar el mensaje.\n\n"
            "Intentá con formato: _TICKER CANTIDAD PRECIO MONEDA_\n"
            "Ejemplo: _SXRS.DE 10 44.50 EUR_"
        )
        return 'OK', 200

    ticker = result.get('ticker')
    qty = result.get('qty')
    price = result.get('price')
    currency = result.get('currency', 'EUR')
    notes = result.get('notes', '')

    if ticker:
        ticker_lower = ticker.lower().strip()
        if ticker_lower in TICKERS_CONOCIDOS:
            ticker = TICKERS_CONOCIDOS[ticker_lower]

    if not all([ticker, qty, price]):
        missing = []
        if not ticker: missing.append('ticker')
        if not qty: missing.append('cantidad')
        if not price: missing.append('precio')
        send_whatsapp(from_number,
            "⚠️ Faltan datos: " + ', '.join(missing) + "\n\n"
            "Enviá el mensaje completo con todos los datos."
        )
        return 'OK', 200

    name = NOMBRES.get(ticker.upper(), ticker.upper())

    try:
        cost_dkk = append_transaction(ticker, name, float(qty), float(price), currency, notes)
        send_whatsapp(from_number,
            "✅ *Transacción agregada*\n\n"
            "📈 " + name + " (" + ticker + ")\n"
            "🔢 Cantidad: " + str(qty) + "\n"
            "💰 Precio: " + str(price) + " " + currency + "\n"
            "🇩🇰 Costo: ~" + str(int(cost_dkk)) + " DKK\n\n"
            "Ya está en tu Google Sheet 📊"
        )
    except Exception as e:
        send_whatsapp(from_number,
            "❌ Error al guardar: " + str(e)
        )

    return 'OK', 200

@app.route('/', methods=['GET'])
def health():
    return 'Portfolio Bot funcionando ✅', 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

from flask import Flask, request
import gspread
from google.oauth2.service_account import Credentials
from twilio.rest import Client
import os, json, re, requests
from datetime import datetime

app = Flask(__name__)

TWILIO_SID   = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM  = os.environ.get('TWILIO_FROM', '')
GEMINI_KEY   = os.environ.get('GEMINI_KEY', '')
SHEET_ID     = os.environ.get('SHEET_ID', '')
SHEET_CREDS  = os.environ.get('SHEET_CREDS', '')

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
    'SPVIBGKL.CO': 'Sparindex INDEX Baereditygtige Global KL',
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
    url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}'
    prompt = (
        'Sos un asistente financiero. Extrae los datos de compra de esta transaccion.\n\n'
        'Devuelve SOLO un JSON valido con este formato exacto (sin markdown, sin explicacion):\n'
        '{"ticker": "SXRS.DE", "qty": 10, "price": 44.50, "currency": "EUR", "notes": ""}\n\n'
        'Reglas:\n'
        '- ticker: el simbolo exacto si lo dice, o el nombre del activo\n'
        '- qty: cantidad de unidades compradas (numero)\n'
        '- price: precio por unidad (numero)\n'
        '- currency: EUR, USD, DKK o ARS\n'
        '- notes: cualquier nota adicional\n\n'
        'Si no podes extraer algun campo, ponelo como null.\n'
        'Si no es una transaccion de compra, devuelve {"error": "no es una transaccion"}\n\n'
        'Mensaje:\n' + text
    )
    try:
        resp = requests.post(url, json={
            'contents': [{'parts': [{'text': prompt}]}]
        }, timeout=30)
        data = resp.json()
        raw = data['candidates'][0]['content']['parts'][0]['text'].strip()
        raw = re.sub(r'```json|```', '', raw).strip()
        return json.loads(raw)
    except Exception as e:
        return {'error': str(e)}

def send_whatsapp(to, message):
    client = Client(TWILIO_SID, TWILIO_TOKEN)
    client.messages.create(body=message, from_=TWILIO_FROM, to=to)

@app.route('/webhook', methods=['POST'])
def webhook():
    from_number = request.form.get('From', '')
    body = request.form.get('Body', '').strip()

    if body.lower() in ['ayuda', 'help', 'hola', '?']:
        send_whatsapp(from_number,
            'Portfolio Bot\n\n'
            'Formatos validos:\n'
            'SXRS.DE 10 44.50 EUR\n'
            'compre 10 acciones de bitcoin a 95000 USD'
        )
        return 'OK', 200

    result = parse_with_gemini(body)

    if 'error' in result:
        send_whatsapp(from_number,
            'No pude interpretar el mensaje.\n'
            'Ejemplo: SXRS.DE 10 44.50 EUR'
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
        if not ticker:
            missing.append('ticker')
        if not qty:
            missing.append('cantidad')
        if not price:
            missing.append('precio')
        send_whatsapp(from_number, 'Faltan datos: ' + ', '.join(missing))
        return 'OK', 200

    name = NOMBRES.get(ticker.upper(), ticker.upper())

    try:
        cost_dkk = append_transaction(ticker, name, float(qty), float(price), currency, notes)
        send_whatsapp(from_number,
            'Transaccion agregada\n'
            + name + ' (' + ticker + ')\n'
            'Cantidad: ' + str(qty) + '\n'
            'Precio: ' + str(price) + ' ' + currency + '\n'
            'Costo aprox: ' + str(int(cost_dkk)) + ' DKK'
        )
    except Exception as e:
        send_whatsapp(from_number, 'Error al guardar: ' + str(e))

    return 'OK', 200

@app.route('/', methods=['GET'])
def health():
    return 'OK', 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

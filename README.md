# AI Fund Manager — MVP Setup Guide

## Estructura del proyecto

```
ai-fund-manager/
├── backend/               # Python FastAPI backend
│   ├── main.py
│   ├── requirements.txt
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── api/
│   ├── core/
│   ├── models/
│   └── services/
└── ea/
    └── AI_Fund_Manager.mq5  # Expert Advisor MT5
```

---

## 1. Configurar el Backend

### Opción A: Local (desarrollo/test)

```bash
# 1. Ir a la carpeta del backend
cd backend

# 2. Copiar y editar variables de entorno
cp .env.example .env
# Editar .env con tu API key de Gemini y una API key inventada

# 3. Levantar PostgreSQL + Redis con Docker
docker-compose up postgres redis -d

# 4. Instalar dependencias Python
pip install -r requirements.txt

# 5. Arrancar el servidor
uvicorn main:app --reload --port 8000
```

### Opción B: Servidor (Hetzner/Railway)

```bash
# Subir el código al servidor y ejecutar:
docker-compose up -d

# El backend estará en: http://TU-IP:8000
```

### Obtener API Key de Gemini (gratis)
1. Ir a https://aistudio.google.com/app/apikey
2. Crear proyecto y generar API key
3. El plan gratuito incluye **500 RPD de Gemini 2.0 Flash Lite** → suficiente para swing trading

---

## 2. Verificar que el backend funciona

```bash
# Health check
curl http://localhost:8000/api/v1/health

# Test del endpoint principal (reemplaza TU_API_KEY)
curl -X POST http://localhost:8000/api/v1/portfolio/review \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: TU_API_KEY" \
  -d '{
    "account": {"balance": 1000, "equity": 1000, "margin_free": 1000, "currency": "USD"},
    "positions": [],
    "config": {"max_positions": 8, "max_margin_pct": 50, "max_per_asset_pct": 15}
  }'
```

> ⚠️ El screener descarga datos de yfinance (~690 símbolos). La primera llamada tarda 30-60 segundos. Las siguientes se sirven desde caché (Redis).

---

## 3. Configurar el EA en MetaTrader 5

### Copiar el EA
```
Copiar: ea/AI_Fund_Manager.mq5
Destino: [MT5 Data Folder]\MQL5\Experts\AI_Fund_Manager.mq5
```

Para encontrar la carpeta: MetaTrader → Archivo → Abrir carpeta de datos

### Añadir URL a la whitelist
1. MT5 → Herramientas → Opciones → Expert Advisors
2. Activar "Permitir WebRequest para las siguientes URL"
3. Añadir: `http://localhost:8000` (o la URL de tu servidor)

### Compilar el EA
1. Abrir MetaEditor (F4)
2. Abrir el archivo `AI_Fund_Manager.mq5`
3. Compilar (F7) — debe compilar sin errores

### Adjuntar a un gráfico
1. Abrir cualquier gráfico en MT5 (ej: EURUSD H4)
2. Arrastrar el EA al gráfico
3. Configurar parámetros:
   - **Backend URL**: `http://localhost:8000` (o tu servidor)
   - **API Key**: la misma que pusiste en `.env`
   - **Poll interval**: 240 minutos (4 horas, recomendado)
   - **Max Positions**: 8
   - **Max Margin %**: 50

---

## 4. Parámetros del EA

| Parámetro | Recomendado | Descripción |
|---|---|---|
| Backend URL | `http://servidor:8000` | URL del backend |
| API Key | (secreta) | Misma que en .env |
| Poll Minutes | 240 | Cada 4h para swing |
| Max Positions | 8 | Máx posiciones abiertas |
| Max Margin % | 50 | Máx % margen usado |
| Max Per Asset % | 15 | Máx % por activo |
| Max Daily DD % | 5 | Parada de emergencia |
| Default SL % | 5 | SL si IA no especifica |
| Default TP % | 10 | TP si IA no especifica |

---

## 5. Monitorización

El EA muestra un **dashboard** en el gráfico con:
- Balance / Equity / Margen libre
- Posiciones abiertas / máximo
- Régimen de mercado (BULL/BEAR/CAUTIOUS/NEUTRAL)
- % de cash objetivo
- ID de la última evaluación
- Countdown a próximo poll
- Estado de conexión

Los logs del backend están en la consola de uvicorn.
Las evaluaciones históricas se guardan en PostgreSQL (`portfolio_evaluations`).

---

## 6. Flujo completo

```
Cada 4h:
EA (MT5) → POST /api/v1/portfolio/review (cuenta + posiciones)
Backend → Screener técnico (690 → 30 candidatos, sin IA)
Backend → Gemini 2.0 Flash Lite (30 candidatos → decisiones portfolio)
Backend → Guardar en PostgreSQL
Backend → Cache Redis (1h TTL)
EA ← Recibe: [{BUY NVDA 10%}, {SELL TLT 8%}, ...]
EA → SymbolSelect() para nuevos símbolos
EA → Ejecutar órdenes con SL/TP calculados
EA → Actualizar dashboard
```

---

## 7. Costes estimados

| Concepto | Coste |
|---|---|
| Gemini 2.0 Flash Lite | **GRATIS** (500 RPD) |
| PostgreSQL + Redis | Incluido en servidor |
| Servidor (Hetzner CX22) | **€4.5/mes** |
| **Total** | **~€5/mes** |

---

## Próximos pasos (Fase 2)

- [ ] Dashboard más elaborado con tabla de posiciones
- [ ] Datos fundamentales (earnings calendar)
- [ ] Análisis de sentimiento de noticias
- [ ] API para ver historial de evaluaciones desde el navegador
- [ ] Backtesting de decisiones pasadas

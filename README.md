# macbook-buy

監控 MacBook 在台灣多通路的補貨狀態,有貨就 Telegram 通知。每次檢查也會抓當下價格,寫入 `state.json` 並附在通知訊息裡。

**價格比較**:以 Apple 直營為基準,非 Apple 的 listing 在 yaml 中用 `baseline_part:` 對應到 Apple 料號,monitor 會自動算出「比 Apple 便宜 NT$X」並顯示在 console / state / 補貨通知。

預設監控目標:**MacBook Pro 14" M5 24G/1TB 太空黑(`MDE34TA/A`) + 銀色(`MDE64TA/A`)**

## 目前實作狀態

| 平台 | Adapter | 訊號來源 |
|------|---------|---------|
| Apple 官網(全台 Apple Store 取貨可用性 + 價格) | [adapters/apple.py](adapters/apple.py) | pickup-message API + 商品頁 HTML |
| PChome 24h(庫存 + 價格) | [adapters/pchome.py](adapters/pchome.py) | ecapi 內部 API(JSONP) |
| Studio A(分色庫存 + 價格) | [adapters/studioa.py](adapters/studioa.py) | 商品頁 HTML 內嵌的 base64 state blob |
| momo | `adapters/momo.py` | ⏳ TODO(計劃用 Playwright,反爬最兇) |

## 安裝與本地測試

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 不設環境變數也能跑,只是不會真的發 Telegram,會印在 console
python monitor.py
```

連續跑兩次,第二次才會在「上次缺貨 → 這次有貨」時觸發通知(第一次只建 baseline)。

## Telegram 設定

1. 跟 [@BotFather](https://t.me/BotFather) 對話,`/newbot` 拿 token
2. 自己跟新 bot 講一句話,然後打:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. 在回應裡找 `chat.id`(整數)

本地測試前在 PowerShell 設環境變數:

```powershell
$env:TELEGRAM_BOT_TOKEN = "12345:abcdef..."
$env:TELEGRAM_CHAT_ID = "123456789"
python monitor.py
```

## 部署到 GitHub Actions

1. 推上 GitHub repo
2. Settings → Secrets and variables → Actions,新增兩個 secret:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. 進 Actions 頁手動 run 一次 `stock-check` workflow 確認綠燈
4. 之後每 20 分鐘自動跑(實際延遲常 5–15 分鐘,免費版限制)

狀態用 `actions/cache` 存,每次 run 寫一個獨立 key、restore 時用前綴模糊配對,讀到的就是最近一次寫入的。

## 通知觸發規則

只在「**有貨 且 比 Apple 直營便宜**」的狀態 false → true 翻轉時發訊息(避免洗版)。

每次檢查算出 `eligible = in_stock and discount_vs_apple > 0`。把 `eligible` 跟上次比:

| 上次 eligible | 這次 eligible | 動作 |
|---------------|--------------|------|
| false / 未知   | **true**     | 🔔 推播 |
| true          | true         | 不推(continuous,避免洗版) |
| *             | false        | 不推 |
| 連續 3 次 error | —           | 推一次警告 |

幾個語意上的副作用:

- **Apple 自身永遠不推**:Apple 是 baseline,沒有 discount 可言
- **同價的通路不推**:例如 Studio A 跟 Apple 同價 → 沒折扣 → 不推
- **缺貨期間有折扣不推**:價格低但拿不到 → 沒意義
- **折扣消失再恢復會再推一次**:例如漲到跟 Apple 同價後又降回來

## 換 / 加商品

編 `products.yaml`,參考檔內註解。

### Apple 料號怎麼找
1. 到 [apple.com/tw](https://www.apple.com/tw) 選好機型 → 加入購物車前的最後一頁
2. View Source(`Ctrl+U`)搜 `partNumber`,例如 `"partNumber":"MDE34TA/A"`

### Apple 限定店點
拿 `storeNumber`(信義 A13 = `R713`、台北 101 = `R486`)填到 `stores: ["R713"]`。
店號可從 https://www.apple.com/tw/retail/storelist/ 反查,留空 = 全台都算。

### PChome 商品 ID
直接複製 24h 商品頁網址內的 ID(`/prod/<ID>`)。
同一規格常有多個 listing(不同賣家/促銷),挑你願意買的那個關注。

### Studio A
複製商品頁完整網址(`/products/<slug>`)。多色商品同 URL,用 `color: 太空黑` / `color: 銀色` 限定;留空 = 任一色有就算。

## 檔案結構

```
.
├── adapters/
│   ├── apple.py
│   ├── pchome.py
│   ├── studioa.py
│   └── __init__.py
├── monitor.py         # 主程式
├── notify.py          # Telegram
├── products.yaml      # 商品清單
├── state.json         # (執行時產生 / cache 保存)
├── requirements.txt
├── .github/workflows/check.yml
└── README.md
```

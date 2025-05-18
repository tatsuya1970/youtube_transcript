# YouTube Video Summarizer

YouTube動画の字幕を取得し、AIを使用して要約・翻訳を行うWebアプリケーションです。

## 機能

- YouTube動画の字幕を自動取得
- 字幕の要約生成
- 日本語/英語の字幕に対応
- レート制限を考慮した安定した処理

## 必要条件

- Python 3.8以上
- Anthropic APIキー

## インストール

1. リポジトリをクローン:
```bash
git clone https://github.com/tatsuya1970/youtube_transcript.git
cd youtube_transcript
```

2. 仮想環境を作成して有効化:
```bash
python -m venv venv
source venv/bin/activate  # Linuxの場合
# または
.\venv\Scripts\activate  # Windowsの場合
```

3. 依存パッケージをインストール:
```bash
pip install -r requirements.txt
```

4. 環境変数の設定:
`.env`ファイルを作成し、以下の内容を追加:
```
ANTHROPIC_API_KEY=your_api_key_here
```

## 使用方法

1. アプリケーションを起動:
```bash
python app.py
```

2. ブラウザで `http://localhost:5001` にアクセス

3. YouTube動画のURLを入力して要約を生成

## 技術スタック

- Flask: Webフレームワーク
- yt-dlp: YouTube動画情報取得
- Anthropic Claude API: 要約生成
- HTML/JavaScript: フロントエンド

## ライセンス

MIT License

## 注意事項

- Anthropic APIの利用制限に注意してください
- 大量のリクエストを送信する場合は、レート制限に注意してください
- このアプリケーションは個人利用を目的としています

from flask import Flask, render_template, request, jsonify
import yt_dlp
import os
import anthropic
from dotenv import load_dotenv
import requests
import re
import logging
import time
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)
client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

# レート制限を管理するためのクラス
class RateLimiter:
    def __init__(self, tokens_per_minute=40000):
        self.tokens_per_minute = tokens_per_minute
        self.tokens_used = 0
        self.last_reset = datetime.now()
        self.minute_window = timedelta(minutes=1)
        self.min_wait_time = 2  # 最小待機時間（秒）

    def wait_if_needed(self, estimated_tokens):
        now = datetime.now()
        if now - self.last_reset >= self.minute_window:
            self.tokens_used = 0
            self.last_reset = now
        
        if self.tokens_used + estimated_tokens > self.tokens_per_minute:
            wait_time = max(
                (self.minute_window - (now - self.last_reset)).total_seconds(),
                self.min_wait_time
            )
            logging.info(f"Waiting for {wait_time:.1f} seconds to respect rate limit...")
            time.sleep(wait_time)
            self.tokens_used = 0
            self.last_reset = datetime.now()
        else:
            # 常に最小待機時間を確保
            time.sleep(self.min_wait_time)
        
        self.tokens_used += estimated_tokens

# グローバルなレートリミターのインスタンスを作成
rate_limiter = RateLimiter()

def vtt_to_text(vtt_content):
    # VTTファイルからテキスト部分だけを抽出
    lines = vtt_content.splitlines()
    text_lines = []
    for line in lines:
        if re.match(r'^[0-9]+:[0-9]+:[0-9]+\.[0-9]+ -->', line):
            continue  # タイムスタンプ行はスキップ
        if line.strip() == '' or line.startswith('WEBVTT'):
            continue
        text_lines.append(line)
    return '\n'.join(text_lines)

def get_video_info(url):
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['ja', 'en'],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            subs = info.get('subtitles', {}) or info.get('automatic_captions', {})
            for lang in ['ja', 'en']:
                if lang in subs:
                    for sub in subs[lang]:
                        if sub.get('ext') == 'vtt':
                            vtt = requests.get(sub['url']).text
                            text = vtt_to_text(vtt)
                            if text:
                                return {
                                    'title': info.get('title', ''),
                                    'subtitle_text': text
                                }
            return {'error': 'No subtitles found'}
        except Exception as e:
            return {'error': str(e)}

@app.route('/')
def index():
    return render_template('index.html')

def estimate_tokens(text):
    """
    より正確なトークン数推定
    日本語の場合は文字数 * 2、英語の場合は単語数 * 1.3 で概算
    """
    # 日本語と英語の文字を区別
    jp_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en_words = len(text.split()) - jp_chars
    
    # 日本語は1文字あたり約2トークン、英語は1単語あたり約1.3トークンと仮定
    return (jp_chars * 2) + (en_words * 1.3)

def split_subtitles(subt_text, max_tokens=2000):  # 3000から2000に変更
    lines = subt_text.splitlines()
    sections = []
    current_section = []
    current_length = 0
    
    # より大きなセクションを作成するために、段落単位で分割
    paragraph = []
    for line in lines:
        if not line.strip():  # 空行の場合
            if paragraph:
                # 段落を1つの文字列に結合
                paragraph_text = " ".join(paragraph)
                paragraph_tokens = estimate_tokens(paragraph_text)
                
                # 段落が大きすぎる場合はさらに分割
                if paragraph_tokens > max_tokens:
                    # 段落を文単位で分割
                    sentences = re.split(r'[。．！？!?]', paragraph_text)
                    current_sentence = []
                    current_sentence_tokens = 0
                    
                    for sentence in sentences:
                        sentence = sentence.strip()
                        if not sentence:
                            continue
                            
                        sentence_tokens = estimate_tokens(sentence)
                        # 文が大きすぎる場合はさらに分割
                        if sentence_tokens > max_tokens:
                            # カンマや読点で分割
                            sub_sentences = re.split(r'[、,，]', sentence)
                            for sub_sentence in sub_sentences:
                                sub_sentence = sub_sentence.strip()
                                if not sub_sentence:
                                    continue
                                    
                                sub_tokens = estimate_tokens(sub_sentence)
                                if current_sentence_tokens + sub_tokens > max_tokens:
                                    if current_sentence:
                                        sections.append(" ".join(current_sentence))
                                    current_sentence = [sub_sentence]
                                    current_sentence_tokens = sub_tokens
                                else:
                                    current_sentence.append(sub_sentence)
                                    current_sentence_tokens += sub_tokens
                        else:
                            if current_sentence_tokens + sentence_tokens > max_tokens:
                                if current_sentence:
                                    sections.append(" ".join(current_sentence))
                                current_sentence = [sentence]
                                current_sentence_tokens = sentence_tokens
                            else:
                                current_sentence.append(sentence)
                                current_sentence_tokens += sentence_tokens
                    
                    if current_sentence:
                        sections.append(" ".join(current_sentence))
                else:
                    if current_length + paragraph_tokens > max_tokens:
                        if current_section:
                            sections.append("\n".join(current_section))
                        current_section = [paragraph_text]
                        current_length = paragraph_tokens
                    else:
                        current_section.append(paragraph_text)
                        current_length += paragraph_tokens
                paragraph = []
        else:
            paragraph.append(line)
    
    # 最後の段落を処理
    if paragraph:
        paragraph_text = " ".join(paragraph)
        if current_section:
            current_section.append(paragraph_text)
            sections.append("\n".join(current_section))
        else:
            sections.append(paragraph_text)
    
    return sections

def summarize_section(client, title, section):
    # プロンプトの長さを制限
    max_prompt_tokens = 150000  # 安全マージンを確保
    estimated_tokens = estimate_tokens(section)
    
    if estimated_tokens > max_prompt_tokens:
        # セクションが大きすぎる場合はさらに分割
        subsections = split_subtitles(section, max_tokens=max_prompt_tokens)
        subsection_summaries = []
        for subsection in subsections:
            subsection_summary = summarize_section(client, title, subsection)
            subsection_summaries.append(subsection_summary)
        return " ".join(subsection_summaries)
    
    prompt = f"動画「{title}」の字幕セクションを簡潔に要約してください（200字以内）:\n\n{section}"
    try:
        # レート制限の確認
        estimated_tokens = estimate_tokens(prompt)
        rate_limiter.wait_if_needed(estimated_tokens)
        
        response = client.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        if "rate_limit_error" in str(e):
            logging.warning("Rate limit hit, waiting for 30 seconds...")
            time.sleep(30)
            return summarize_section(client, title, section)  # 再試行
        logging.error("summarize_section error: %s", e)
        raise

def summarize_all(client, title, summaries):
    # プロンプトの長さを制限
    max_prompt_tokens = 150000  # 安全マージンを確保
    combined_summaries = "\n\n".join(summaries)
    estimated_tokens = estimate_tokens(combined_summaries)
    
    if estimated_tokens > max_prompt_tokens:
        # 要約が大きすぎる場合は分割して処理
        summary_sections = split_subtitles(combined_summaries, max_tokens=max_prompt_tokens)
        section_summaries = []
        
        for i, section in enumerate(summary_sections, 1):
            logging.info(f"Processing final summary section {i}/{len(summary_sections)}")
            try:
                section_summary = summarize_section(client, title, section)
                section_summaries.append(section_summary)
                # セクション間の待機時間
                if i < len(summary_sections):
                    time.sleep(5)
            except Exception as e:
                if "rate_limit_error" in str(e):
                    logging.warning("Rate limit hit in final summary, waiting for 30 seconds...")
                    time.sleep(30)
                    return summarize_all(client, title, summaries)  # 再試行
                raise
        
        # 最終的な要約を生成
        final_prompt = f"動画「{title}」の部分的な要約を元に、全体を簡潔に要約してください（500字以内）:\n\n" + "\n\n".join(section_summaries)
        try:
            response = client.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=1000,
                messages=[{"role": "user", "content": final_prompt}]
            )
            return response.content[0].text
        except Exception as e:
            logging.error("Final summary generation error: %s", e)
            raise
    
    prompt = f"動画「{title}」の各セクションの要約を元に、全体を簡潔に要約してください（500字以内）:\n\n{combined_summaries}"
    try:
        # レート制限の確認
        estimated_tokens = estimate_tokens(prompt)
        rate_limiter.wait_if_needed(estimated_tokens)
        
        response = client.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        if "rate_limit_error" in str(e):
            logging.warning("Rate limit hit, waiting for 30 seconds...")
            time.sleep(30)
            return summarize_all(client, title, summaries)  # 再試行
        logging.error("summarize_all error: %s", e)
        raise

def process_batch(client, title, sections, batch_size=1):  # バッチサイズを2から1に変更
    """バッチ単位でセクションを処理する"""
    summaries = []
    for i in range(0, len(sections), batch_size):
        batch = sections[i:i + batch_size]
        logging.info(f"Processing batch {i//batch_size + 1}/{(len(sections) + batch_size - 1)//batch_size}")
        
        batch_summaries = []
        for j, section in enumerate(batch, 1):
            try:
                summary = summarize_section(client, title, section)
                batch_summaries.append(summary)
                logging.info(f"Completed section {i + j}/{len(sections)}")
            except Exception as e:
                if "rate_limit_error" in str(e):
                    logging.warning("Rate limit hit, waiting for 30 seconds...")
                    time.sleep(30)
                    return process_batch(client, title, sections[i:], batch_size)  # 残りのセクションを再処理
                raise
        
        summaries.extend(batch_summaries)
        # バッチ間の待機時間を増やす
        if i + batch_size < len(sections):
            time.sleep(15)  # 10秒から15秒に増加
    
    return summaries

@app.route('/process', methods=['POST'])
def process_video():
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    try:
        # Get video info and subtitles
        video_info = get_video_info(url)
        if 'error' in video_info:
            return jsonify({'error': video_info['error']}), 400

        subtitle_text = video_info['subtitle_text']
        title = video_info['title']

        # 字幕テキストを分割（より大きなセクションに）
        sections = split_subtitles(subtitle_text, max_tokens=3000)
        logging.info(f"Split video into {len(sections)} sections")
        
        # バッチ処理で要約を取得
        summaries = process_batch(client, title, sections)
        
        # 全体の要約を生成
        logging.info("Generating final summary...")
        final_summary = summarize_all(client, title, "\n\n".join(summaries))
        
        return jsonify({
            'title': title,
            'transcript': subtitle_text,
            'summary': final_summary
        })
    except Exception as e:
        logging.error("process_video error: %s", e)
        return jsonify({'error': f'サーバーエラー: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 
#!/usr/bin/env python3
#
# scrcpy 本地网页控制台。
#
# 它有意只包裹官方 scrcpy 和 adb 可执行文件，不重写 scrcpy 协议。
# 这样既能把常用命令做成按钮，也更容易跟随上游 scrcpy 更新。

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 27190
CONTROL_PANEL_VERSION = "0.3.1"
DEFAULT_SCREENSHOT_DIR = Path.home() / "Pictures" / "scrcpy-control-panel"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "scrcpy-control-panel"
DEFAULT_DEVICE_DIR = "/sdcard/Download"
FILE_TRANSFER_TIMEOUT = 900
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024
DOWNLOAD_DIR = DEFAULT_DOWNLOAD_DIR
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "scrcpy-control-panel"
SCRCPY_LOG_TAIL_BYTES = 12000
ADB_CONNECT_INITIAL_WAIT_TIMEOUT = 2.0
ADB_CONNECT_WAIT_TIMEOUT = 8.0
ADB_CONNECT_POLL_INTERVAL = 0.4
ADB_RECONNECT_WAIT_TIMEOUT = 3.0
ADB_COMMAND_LOCK = threading.RLock()


KEYEVENTS = {
    "back": "4",
    "home": "3",
    "apps": "187",
    "power": "26",
    "wake": "224",
    "sleep": "223",
    "volume_up": "24",
    "volume_down": "25",
    "mute": "164",
}


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>scrcpy 控制台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #202329;
      --muted: #68707d;
      --line: #d8dde5;
      --accent: #176b87;
      --accent-2: #2d7d46;
      --warn: #a45a00;
      --danger: #b42318;
      --shadow: 0 12px 35px rgba(22, 29, 40, .10);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
    }
    header {
      min-height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 22px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .92);
      position: sticky;
      top: 0;
      z-index: 5;
      backdrop-filter: blur(8px);
    }
    h1 {
      margin: 0;
      font-size: 20px;
      font-weight: 680;
      letter-spacing: 0;
    }
    .header-brand {
      display: flex;
      align-items: center;
      gap: 9px;
    }
    .version-badge {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      color: var(--muted);
      background: #fff;
      font-size: 11px;
      font-weight: 700;
      white-space: nowrap;
    }
    .header-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
    }
    main {
      display: grid;
      grid-template-columns: minmax(300px, 420px) minmax(420px, 1fr);
      gap: 16px;
      align-items: start;
      padding: 16px;
      max-width: 1440px;
      margin: 0 auto;
    }
    .workflow-column {
      display: grid;
      gap: 12px;
      align-content: start;
      min-width: 0;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
    }
    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 14px 14px 10px;
      border-bottom: 1px solid var(--line);
    }
    h2 {
      margin: 0;
      font-size: 15px;
      font-weight: 680;
      letter-spacing: 0;
    }
    .body { padding: 14px; }
    .grid { display: grid; gap: 10px; }
    .grid.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .grid.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .advanced-options .grid.three,
    .advanced-options .grid.four {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .grid.controls {
      grid-template-columns: repeat(3, minmax(92px, 1fr));
      align-items: stretch;
    }
    label {
      display: grid;
      gap: 6px;
      font-size: 12px;
      color: var(--muted);
      font-weight: 620;
    }
    input, select {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
      font-size: 14px;
    }
    input[type="checkbox"] {
      width: 18px;
      min-height: 18px;
      accent-color: var(--accent);
    }
    .check {
      display: flex;
      align-items: center;
      gap: 9px;
      min-height: 38px;
      color: var(--text);
      font-size: 14px;
      font-weight: 560;
    }
    button {
      appearance: none;
      border: 1px solid var(--line);
      border-radius: 6px;
      min-height: 38px;
      padding: 8px 10px;
      color: var(--text);
      background: #fff;
      font: inherit;
      font-size: 14px;
      font-weight: 650;
      cursor: pointer;
      transition: transform .08s ease, border-color .12s ease, background .12s ease;
      white-space: nowrap;
    }
    button:hover { border-color: #9aa5b4; background: #fbfcfd; }
    button:active { transform: translateY(1px); }
    button:disabled {
      cursor: not-allowed;
      opacity: .46;
      transform: none;
      background: #f1f3f5;
      border-color: #d8dde5;
      color: #7b8491;
    }
    button:disabled:hover { background: #f1f3f5; border-color: #d8dde5; }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }
    button.good {
      background: var(--accent-2);
      border-color: var(--accent-2);
      color: white;
    }
    button.warn {
      background: #fff8ee;
      border-color: #e1b372;
      color: var(--warn);
    }
    button.danger {
      background: #fff4f2;
      border-color: #edaca4;
      color: var(--danger);
    }
    button.danger.solid {
      background: var(--danger);
      border-color: var(--danger);
      color: #fff;
    }
    button.next-button {
      min-height: 48px;
      font-size: 16px;
    }
    .target-button {
      border-color: #78b7c9;
      box-shadow: 0 0 0 3px rgba(23, 107, 135, .16);
    }
    .wide { grid-column: 1 / -1; }
    .hidden { display: none !important; }
    .next-card {
      border-color: #b9dce6;
      background: #f7fcfd;
    }
    .next-title {
      font-size: 18px;
      font-weight: 760;
      letter-spacing: 0;
    }
    .next-detail {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
      margin-top: 4px;
    }
    .locked-panel {
      opacity: .48;
      pointer-events: none;
      user-select: none;
    }
    .locked-message {
      margin: 14px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 16px 14px;
      color: var(--muted);
      background: #fafbfc;
      font-size: 14px;
      font-weight: 620;
      text-align: center;
    }
    .control-button {
      min-height: 58px;
      display: grid;
      align-content: center;
      gap: 4px;
    }
    .control-button span {
      display: block;
      font-size: 11px;
      color: var(--muted);
      font-weight: 520;
    }
    .status {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
      min-width: 0;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 26px;
      padding: 4px 9px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      font-weight: 640;
    }
    .pill.ok { color: var(--accent-2); border-color: #9bd0ab; }
    .pill.bad { color: var(--danger); border-color: #edaca4; }
    .guide {
      max-width: 1440px;
      margin: 14px auto 0;
      padding: 0 16px;
      display: grid;
      gap: 10px;
    }
    .steps {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .step {
      min-height: 54px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 9px 10px;
      display: grid;
      gap: 3px;
    }
    .step strong {
      font-size: 13px;
      font-weight: 700;
    }
    .step span {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .step.active {
      border-color: #78b7c9;
      background: #eef8fb;
    }
    .step.done {
      border-color: #9bd0ab;
      background: #f2fbf5;
    }
    .hint {
      border: 1px solid #f0d18b;
      background: #fffaf0;
      color: #5f4200;
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 14px;
      font-weight: 600;
    }
    .muted { color: var(--muted); }
    .spacer { height: 10px; }
    .advanced-options {
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }
    .advanced-options summary {
      color: var(--muted);
      cursor: pointer;
      font-size: 13px;
      font-weight: 680;
      list-style-position: inside;
    }
    .advanced-options[open] summary { margin-bottom: 10px; }
    .param-group {
      border-top: 1px solid var(--line);
      padding-top: 12px;
      display: block;
    }
    .param-group:first-of-type {
      border-top: 0;
      padding-top: 0;
    }
    .param-group[open] {
      display: grid;
      gap: 10px;
    }
    .param-title {
      color: var(--text);
      cursor: pointer;
      font-size: 13px;
      font-weight: 720;
      list-style-position: inside;
    }
    .param-group[open] > .param-title {
      margin-bottom: 2px;
    }
    .transfer-heading {
      margin-top: 2px;
      font-size: 12px;
      font-weight: 700;
      color: var(--muted);
    }
    .drop-zone {
      display: grid;
      gap: 5px;
      border: 1.5px dashed #b9c2cf;
      border-radius: 8px;
      padding: 18px 14px;
      text-align: center;
      background: #fafbfc;
      cursor: pointer;
      transition: border-color .12s ease, background .12s ease;
    }
    .drop-zone:hover, .drop-zone:focus-visible, .drop-zone.dragging {
      border-color: var(--accent);
      background: #eef6f9;
      outline: none;
    }
    .drop-zone.disabled { cursor: progress; opacity: .6; }
    .drop-zone strong { font-size: 13px; font-weight: 640; }
    .drop-zone .field-note { word-break: break-word; }
    .file-list {
      border: 1px solid var(--line);
      border-radius: 6px;
      max-height: 280px;
      overflow: auto;
      background: #fff;
    }
    .file-list-header {
      position: sticky;
      top: 0;
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      background: #fafbfc;
      color: var(--muted);
      font-size: 11px;
      font-weight: 620;
      word-break: break-all;
    }
    .file-list-empty {
      padding: 14px 10px;
      color: var(--muted);
      font-size: 13px;
      text-align: center;
    }
    .file-row {
      display: grid;
      grid-template-columns: 36px minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      width: 100%;
      min-height: 0;
      border: 0;
      border-bottom: 1px solid #eef1f5;
      border-radius: 0;
      padding: 7px 10px;
      background: #fff;
      text-align: left;
      font-size: 13px;
      font-weight: 540;
      white-space: normal;
    }
    .file-row:last-child { border-bottom: 0; }
    .file-row:hover { background: #f4f7fa; border-color: #eef1f5; }
    .file-row:active { transform: none; }
    .file-row.selected { background: #e6f1f5; }
    .file-row:disabled { opacity: .7; background: #fff; }
    .file-row .kind {
      font-size: 10px;
      font-weight: 700;
      color: var(--muted);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 1px 4px;
      text-align: center;
    }
    .file-row.dir .kind { color: var(--accent); border-color: #a9cdd9; }
    .file-row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .file-row .size { color: var(--muted); font-size: 11px; white-space: nowrap; }
    .field-note {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
      font-weight: 520;
    }
    pre {
      margin: 0;
      min-height: 180px;
      max-height: 320px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #141820;
      color: #dce4ee;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      .steps { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 720px) {
      header { align-items: flex-start; flex-direction: column; }
      .header-actions { justify-content: flex-start; }
      .status { justify-content: flex-start; }
      main { grid-template-columns: 1fr; padding: 10px; }
      .guide { padding: 0 10px; }
      .steps { grid-template-columns: 1fr; }
      .grid.three, .grid.four, .grid.controls { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 520px) {
      .grid.two, .grid.three, .grid.four { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-brand">
      <h1>scrcpy 控制台</h1>
      <span id="panelVersion" class="version-badge" title="当前后端控制台版本">版本检测中</span>
    </div>
    <div class="header-actions">
      <div class="status">
        <span id="adbStatus" class="pill">adb</span>
        <span id="scrcpyStatus" class="pill">scrcpy</span>
        <span id="processStatus" class="pill">process</span>
      </div>
      <button id="shutdownPanelBtn" class="danger" title="停止 scrcpy 并关闭本地网页控制台服务">关闭控制台</button>
    </div>
  </header>
  <div class="guide">
    <div class="steps" aria-label="推荐流程">
      <div id="step1" class="step active"><strong>1. 检测设备</strong><span>刷新 ADB 设备列表</span></div>
      <div id="step2" class="step"><strong>2. 建立连接</strong><span>USB 开 TCP/IP 或连接 Wi-Fi</span></div>
      <div id="step3" class="step"><strong>3. 启动投屏</strong><span>选择参数后启动 scrcpy</span></div>
      <div id="step4" class="step"><strong>4. 操控手机</strong><span>按钮会发送 ADB 控制命令</span></div>
    </div>
    <div id="actionHint" class="hint">正在检测本机 adb、scrcpy 和已连接设备...</div>
  </div>
  <main>
    <div class="workflow-column" aria-label="连接与启动">
      <section class="next-card">
        <div class="body grid">
          <div>
            <div id="nextTitle" class="next-title">下一步</div>
            <div id="nextDetail" class="next-detail">正在检测本机环境和手机连接状态...</div>
          </div>
          <button id="nextActionBtn" class="primary next-button" title="执行当前推荐操作">刷新设备</button>
        </div>
      </section>

      <section>
        <div class="section-header">
          <h2>连接</h2>
          <button id="refreshBtn" title="刷新 ADB 设备，并自动连接已保存的无线地址">刷新</button>
        </div>
        <div class="body grid">
          <label>设备
            <select id="serialSelect"></select>
          </label>
          <label>无线地址
            <input id="wirelessAddress" placeholder="192.168.3.41:5555" autocomplete="off">
          </label>
          <div class="grid two">
            <button id="connectBtn" class="primary wide" title="连接无线 ADB 地址">无线连接</button>
          </div>
          <div class="grid three">
            <button id="tcpipBtn" class="warn" title="为已选择的 USB 设备开启 ADB TCP/IP">开启 TCP/IP</button>
            <button id="restartAdbBtn" title="重启本机 ADB 服务，并自动重连无线设备">重启 ADB</button>
            <button id="copySerialBtn" title="复制当前设备序列号">复制序列号</button>
          </div>
          <div id="deviceSummary" class="muted"></div>
        </div>
      </section>

      <section>
        <div class="section-header">
          <h2>启动</h2>
        </div>
        <div class="body grid">
          <button id="startBtn" class="good" title="为当前设备启动 scrcpy">启动 scrcpy</button>
          <details class="advanced-options" open>
            <summary>启动参数</summary>
            <div class="grid">
              <details class="param-group" open>
                <summary class="param-title">常用</summary>
                <div class="grid three">
                  <label title="限制投屏长边尺寸；越小越省带宽和性能。">最大分辨率
                    <select id="maxSize">
                      <option value="">原生</option>
                      <option value="2560">2560</option>
                      <option value="1920">1920</option>
                      <option value="1600">1600</option>
                      <option value="1280">1280</option>
                      <option value="1024">1024</option>
                      <option value="800">800</option>
                    </select>
                    <span class="field-note">长边上限，低一点更流畅。</span>
                  </label>
                  <label title="视频码率；数值越高画面越清晰，也更占网络。">视频码率
                    <select id="bitRate">
                      <option value="">默认</option>
                      <option value="2M">2M</option>
                      <option value="4M">4M</option>
                      <option value="8M">8M</option>
                      <option value="12M">12M</option>
                      <option value="16M">16M</option>
                      <option value="24M">24M</option>
                      <option value="32M">32M</option>
                    </select>
                    <span class="field-note">清晰度和网络占用。</span>
                  </label>
                  <label title="限制最大帧率；留空使用设备默认帧率。">最大帧率
                    <input id="maxFps" type="number" min="1" max="240" step="1" placeholder="60" autocomplete="off">
                    <span class="field-note">卡顿时可设 30 或 60。</span>
                  </label>
                </div>
                <div class="grid three">
                  <label title="禁用音频转发，减少兼容性问题和资源占用。" class="check"><input id="noAudio" type="checkbox" checked>禁用音频</label>
                  <label title="启动后进入全屏。" class="check"><input id="fullscreen" type="checkbox">全屏</label>
                  <label title="让 scrcpy 窗口保持在其他窗口上方。" class="check"><input id="alwaysOnTop" type="checkbox">窗口置顶</label>
                  <label title="启动后关闭手机屏幕，电脑仍显示投屏。" class="check"><input id="turnScreenOff" type="checkbox">启动后息屏</label>
                  <label title="连接期间让设备保持唤醒。" class="check"><input id="stayAwake" type="checkbox">保持亮屏</label>
                  <label title="在设备上显示触摸点，适合演示和录屏。" class="check"><input id="showTouches" type="checkbox">显示触摸点</label>
                </div>
              </details>

              <details class="param-group">
                <summary class="param-title">视频</summary>
                <div class="grid three">
                  <label title="选择视频编码格式；H.264 兼容性最好，H.265/AV1 可能更省码率。">视频编码
                    <select id="videoCodec">
                      <option value="">默认</option>
                      <option value="h264">H.264</option>
                      <option value="h265">H.265</option>
                      <option value="av1">AV1</option>
                    </select>
                    <span class="field-note">优先用默认或 H.264。</span>
                  </label>
                  <label title="选择视频来源；普通投屏选择屏幕，调试相机时选择摄像头。">视频来源
                    <select id="videoSource">
                      <option value="">默认屏幕</option>
                      <option value="display">屏幕</option>
                      <option value="camera">摄像头</option>
                    </select>
                    <span class="field-note">摄像头模式会使用下方相机参数。</span>
                  </label>
                  <label title="增加视频缓冲，弱网时更稳，但延迟会变高。">视频缓冲 ms
                    <input id="videoBuffer" type="number" min="0" step="10" placeholder="0" autocomplete="off">
                    <span class="field-note">低延迟留空或 0。</span>
                  </label>
                </div>
                <div class="grid three">
                  <label title="只投屏屏幕的一部分，格式是 宽:高:x:y。">裁剪区域
                    <input id="crop" placeholder="1080:1920:0:0" autocomplete="off">
                    <span class="field-note">只看局部屏幕时使用。</span>
                  </label>
                  <label title="选择要投屏的 Android display id；可通过 scrcpy --list-displays 查看。">显示 ID
                    <input id="displayId" type="number" min="0" step="1" placeholder="0" autocomplete="off">
                    <span class="field-note">多屏/虚拟屏场景使用。</span>
                  </label>
                  <label title="捕获画面的方向；@ 前缀表示锁定方向。">捕获方向
                    <select id="captureOrientation">
                      <option value="">跟随设备</option>
                      <option value="0">0°</option>
                      <option value="90">90°</option>
                      <option value="180">180°</option>
                      <option value="270">270°</option>
                      <option value="@0">锁定 0°</option>
                      <option value="@90">锁定 90°</option>
                      <option value="@180">锁定 180°</option>
                      <option value="@270">锁定 270°</option>
                    </select>
                    <span class="field-note">横屏应用可锁 90°。</span>
                  </label>
                </div>
                <div class="grid three">
                  <label title="不采集视频；通常用于只转发音频或特殊调试。" class="check"><input id="noVideo" type="checkbox">禁用视频</label>
                  <label title="不在电脑窗口播放视频；常用于只录制不显示。" class="check"><input id="noVideoPlayback" type="checkbox">不播放视频</label>
                  <label title="视频编码出错时不要自动降低分辨率重试。" class="check"><input id="noDownsizeOnError" type="checkbox">失败不降级</label>
                  <label title="禁用 mipmap；少数渲染问题时可尝试。" class="check"><input id="noMipmaps" type="checkbox">禁用 mipmap</label>
                  <label title="在日志中输出 FPS，排查性能时使用。" class="check"><input id="printFps" type="checkbox">输出 FPS</label>
                </div>
              </details>

              <details class="param-group">
                <summary class="param-title">音频</summary>
                <div class="grid three">
                  <label title="只录制/转发音频，不在电脑播放。" class="check"><input id="noAudioPlayback" type="checkbox">不播放音频</label>
                  <label title="复制音频到电脑播放，同时设备也继续播放；通常只适合 playback 音源。" class="check"><input id="audioDup" type="checkbox">设备也播放</label>
                  <label title="如果音频不可用则启动失败，适合需要确认录音/声音链路的场景。" class="check"><input id="requireAudio" type="checkbox">必须有音频</label>
                </div>
                <div class="grid three">
                  <label title="选择音频编码格式；默认通常最稳。">音频编码
                    <select id="audioCodec">
                      <option value="">默认</option>
                      <option value="opus">Opus</option>
                      <option value="aac">AAC</option>
                      <option value="flac">FLAC</option>
                      <option value="raw">Raw</option>
                    </select>
                    <span class="field-note">普通使用保持默认。</span>
                  </label>
                  <label title="选择音频来源；麦克风相关来源需要设备支持。">音频来源
                    <select id="audioSource">
                      <option value="">默认</option>
                      <option value="output">系统输出</option>
                      <option value="playback">应用播放</option>
                      <option value="mic">麦克风</option>
                      <option value="mic-unprocessed">麦克风原始</option>
                      <option value="mic-camcorder">摄像机麦克风</option>
                      <option value="mic-voice-recognition">语音识别</option>
                      <option value="mic-voice-communication">语音通话</option>
                      <option value="voice-call">通话</option>
                    </select>
                    <span class="field-note">默认更少出错。</span>
                  </label>
                  <label title="音频码率；AAC/Opus 可用，留空使用默认。">音频码率
                    <select id="audioBitRate">
                      <option value="">默认</option>
                      <option value="64K">64K</option>
                      <option value="96K">96K</option>
                      <option value="128K">128K</option>
                      <option value="192K">192K</option>
                      <option value="256K">256K</option>
                    </select>
                    <span class="field-note">声音质量和带宽。</span>
                  </label>
                </div>
                <div class="grid two">
                  <label title="音频编码缓冲，增大可减少断续但会增加延迟。">音频缓冲 ms
                    <input id="audioBuffer" type="number" min="0" step="10" placeholder="50" autocomplete="off">
                    <span class="field-note">声音断续时可增大。</span>
                  </label>
                  <label title="电脑播放端缓冲，增大可减少播放断续。">播放缓冲 ms
                    <input id="audioOutputBuffer" type="number" min="0" step="10" placeholder="5" autocomplete="off">
                    <span class="field-note">电脑播放端缓冲。</span>
                  </label>
                </div>
              </details>

              <details class="param-group">
                <summary class="param-title">窗口</summary>
                <div class="grid three">
                  <label title="隐藏窗口边框和标题栏。" class="check"><input id="windowBorderless" type="checkbox">无边框</label>
                </div>
                <div class="grid three">
                  <label title="自定义 scrcpy 窗口标题。">窗口标题
                    <input id="windowTitle" placeholder="Phone" autocomplete="off">
                    <span class="field-note">多设备时方便区分。</span>
                  </label>
                  <label title="窗口内容如何适配窗口大小。">适配方式
                    <select id="renderFit">
                      <option value="">默认</option>
                      <option value="letterbox">保持比例</option>
                      <option value="stretched">拉伸填满</option>
                      <option value="unscaled">原始尺寸</option>
                    </select>
                    <span class="field-note">默认保持比例。</span>
                  </label>
                  <label title="背景颜色，常用于有黑边时。">背景色
                    <input id="backgroundColor" placeholder="000000" autocomplete="off">
                    <span class="field-note">6 位十六进制。</span>
                  </label>
                </div>
                <div class="grid four">
                  <label title="启动窗口宽度，留空由 scrcpy 自动决定。">窗口宽
                    <input id="windowWidth" type="number" min="1" step="1" placeholder="420" autocomplete="off">
                  </label>
                  <label title="启动窗口高度，留空由 scrcpy 自动决定。">窗口高
                    <input id="windowHeight" type="number" min="1" step="1" placeholder="900" autocomplete="off">
                  </label>
                  <label title="窗口左上角 X 坐标。">位置 X
                    <input id="windowX" type="number" step="1" placeholder="自动" autocomplete="off">
                  </label>
                  <label title="窗口左上角 Y 坐标。">位置 Y
                    <input id="windowY" type="number" step="1" placeholder="自动" autocomplete="off">
                  </label>
                </div>
                <div class="grid two">
                  <label title="允许窗口比例不锁定。" class="check"><input id="noWindowAspectLock" type="checkbox">不锁定窗口比例</label>
                  <label title="不显示 scrcpy 窗口，只保留录制或控制等能力。" class="check"><input id="noWindow" type="checkbox">不显示窗口</label>
                </div>
              </details>

              <details class="param-group">
                <summary class="param-title">设备行为</summary>
                <div class="grid three">
                  <label title="通过模拟用户活动保持屏幕活跃。" class="check"><input id="keepActive" type="checkbox">保持活跃</label>
                  <label title="关闭 scrcpy 时让设备关屏。" class="check"><input id="powerOffOnClose" type="checkbox">退出时关屏</label>
                </div>
                <div class="grid three">
                  <label title="启动时不要自动点亮设备屏幕。" class="check"><input id="noPowerOn" type="checkbox">不自动亮屏</label>
                  <label title="禁用电脑系统屏保，避免投屏时电脑睡眠。" class="check"><input id="disableScreensaver" type="checkbox">禁用电脑屏保</label>
                </div>
                <div class="grid two">
                  <label title="设置手机屏幕超时时间，单位秒。">屏幕超时秒
                    <input id="screenOffTimeout" type="number" min="0" step="1" placeholder="300" autocomplete="off">
                    <span class="field-note">控制手机自身熄屏时间。</span>
                  </label>
                  <label title="启动指定应用，填写包名；前缀 + 表示强制停止后再启动。">启动应用
                    <input id="startApp" placeholder="+com.example.app" autocomplete="off">
                    <span class="field-note">可填写 Android 包名。</span>
                  </label>
                </div>
              </details>

              <details class="param-group">
                <summary class="param-title">输入控制</summary>
                <div class="grid three">
                  <label title="选择键盘注入方式；默认 SDK 最通用，UHID 更像物理键盘。">键盘模式
                    <select id="keyboardMode">
                      <option value="">默认</option>
                      <option value="disabled">禁用</option>
                      <option value="sdk">SDK</option>
                      <option value="uhid">UHID</option>
                      <option value="aoa">AOA</option>
                    </select>
                    <span class="field-note">普通使用保持默认。</span>
                  </label>
                  <label title="选择鼠标注入方式；默认 SDK 不会捕获鼠标，UHID/AOA 更像物理鼠标。">鼠标模式
                    <select id="mouseMode">
                      <option value="">默认</option>
                      <option value="disabled">禁用</option>
                      <option value="sdk">SDK</option>
                      <option value="uhid">UHID</option>
                      <option value="aoa">AOA</option>
                    </select>
                    <span class="field-note">普通使用保持默认。</span>
                  </label>
                  <label title="选择手柄注入方式。">手柄模式
                    <select id="gamepadMode">
                      <option value="">默认</option>
                      <option value="disabled">禁用</option>
                      <option value="uhid">UHID</option>
                      <option value="aoa">AOA</option>
                    </select>
                    <span class="field-note">连接手柄时使用。</span>
                  </label>
                </div>
                <div class="grid three">
                  <label title="禁用 scrcpy 自带控制，只看画面；网页 ADB 按钮仍可用。" class="check"><input id="noControl" type="checkbox">禁用窗口控制</label>
                  <label title="粘贴时更倾向输入文本事件。" class="check"><input id="preferText" type="checkbox">优先文本输入</label>
                  <label title="关闭设备剪贴板与电脑剪贴板的自动同步。" class="check"><input id="noClipboardAutosync" type="checkbox">不同步剪贴板</label>
                  <label title="禁用按键长按重复。" class="check"><input id="noKeyRepeat" type="checkbox">禁用按键重复</label>
                  <label title="直接转发原始按键事件，游戏或特殊键盘场景可尝试。" class="check"><input id="rawKeyEvents" type="checkbox">原始按键</label>
                  <label title="不要发送鼠标悬停移动事件。" class="check"><input id="noMouseHover" type="checkbox">禁用鼠标悬停</label>
                  <label title="使用旧版粘贴方式。" class="check"><input id="legacyPaste" type="checkbox">旧版粘贴</label>
                </div>
              </details>

              <details class="param-group">
                <summary class="param-title">录制</summary>
                <div class="grid three">
                  <label title="保存录制文件路径；留空不录制。">录制文件
                    <input id="recordFile" placeholder="/Users/me/Desktop/phone.mp4" autocomplete="off">
                    <span class="field-note">填写后启动即录制。</span>
                  </label>
                  <label title="强制录制格式；通常由文件后缀自动判断。">录制格式
                    <select id="recordFormat">
                      <option value="">自动</option>
                      <option value="mp4">MP4</option>
                      <option value="mkv">MKV</option>
                      <option value="m4a">M4A</option>
                      <option value="mka">MKA</option>
                      <option value="opus">Opus</option>
                      <option value="aac">AAC</option>
                      <option value="flac">FLAC</option>
                      <option value="wav">WAV</option>
                    </select>
                    <span class="field-note">有文件后缀时可留自动。</span>
                  </label>
                  <label title="录制文件的方向。">录制方向
                    <select id="recordOrientation">
                      <option value="">跟随画面</option>
                      <option value="0">0°</option>
                      <option value="90">90°</option>
                      <option value="180">180°</option>
                      <option value="270">270°</option>
                    </select>
                    <span class="field-note">只影响录制文件。</span>
                  </label>
                </div>
              </details>

              <details class="param-group">
                <summary class="param-title">摄像头与虚拟显示</summary>
                <div class="grid three">
                  <label title="摄像头 ID；可用 scrcpy --list-cameras 查看。">摄像头 ID
                    <input id="cameraId" placeholder="0" autocomplete="off">
                    <span class="field-note">视频来源选摄像头时使用。</span>
                  </label>
                  <label title="选择前后摄像头。">摄像头方向
                    <select id="cameraFacing">
                      <option value="">默认</option>
                      <option value="front">前置</option>
                      <option value="back">后置</option>
                      <option value="external">外接</option>
                    </select>
                    <span class="field-note">和摄像头 ID 二选一即可。</span>
                  </label>
                  <label title="摄像头尺寸，格式 宽x高。">摄像头尺寸
                    <input id="cameraSize" placeholder="1920x1080" autocomplete="off">
                    <span class="field-note">设备必须支持该尺寸。</span>
                  </label>
                </div>
                <div class="grid three">
                  <label title="摄像头帧率。">摄像头 FPS
                    <input id="cameraFps" type="number" min="1" max="240" step="1" placeholder="30" autocomplete="off">
                  </label>
                  <label title="摄像头初始变焦。">摄像头变焦
                    <input id="cameraZoom" type="number" min="0" step="0.1" placeholder="1" autocomplete="off">
                  </label>
                  <label title="摄像头宽高比，例如 16:9、4:3 或 sensor。">摄像头比例
                    <input id="cameraAr" placeholder="16:9" autocomplete="off">
                  </label>
                </div>
                <div class="grid three">
                  <label title="启用摄像头手电筒。" class="check"><input id="cameraTorch" type="checkbox">摄像头补光</label>
                  <label title="启用摄像头高速采集模式。" class="check"><input id="cameraHighSpeed" type="checkbox">高速摄像头</label>
                  <label title="创建虚拟显示，格式 宽x高/dpi；只填 /240 也可以。">新建显示
                    <input id="newDisplay" placeholder="1920x1080/420" autocomplete="off">
                    <span class="field-note">留空不创建虚拟显示。</span>
                  </label>
                </div>
                <div class="grid two">
                  <label title="让虚拟显示尺寸跟随窗口变化。" class="check"><input id="flexDisplay" type="checkbox">弹性显示</label>
                  <label title="虚拟显示关闭后保留其中应用内容。" class="check"><input id="noVdDestroyContent" type="checkbox">保留虚拟显示内容</label>
                </div>
              </details>

              <details class="param-group">
                <summary class="param-title">高级</summary>
                <div class="grid three">
                  <label title="运行多久后自动退出，单位秒。">限时秒
                    <input id="timeLimit" type="number" min="0" step="1" placeholder="3600" autocomplete="off">
                    <span class="field-note">定时演示或录制。</span>
                  </label>
                  <label title="scrcpy 退出时是否暂停窗口。">退出暂停
                    <select id="pauseOnExit">
                      <option value="">默认</option>
                      <option value="true">总是</option>
                      <option value="false">从不</option>
                      <option value="if-error">仅错误</option>
                    </select>
                    <span class="field-note">调试启动失败时有用。</span>
                  </label>
                  <label title="日志详细程度。">日志级别
                    <select id="verbosity">
                      <option value="">默认</option>
                      <option value="verbose">Verbose</option>
                      <option value="debug">Debug</option>
                      <option value="info">Info</option>
                      <option value="warn">Warn</option>
                      <option value="error">Error</option>
                    </select>
                    <span class="field-note">排查问题时调高。</span>
                  </label>
                  <label title="拖拽文件到 scrcpy 窗口时推送到设备的目标目录。">推送目录
                    <input id="pushTarget" placeholder="/sdcard/Download/" autocomplete="off">
                    <span class="field-note">用于拖拽安装/传文件。</span>
                  </label>
                </div>
                <div class="grid three">
                  <label title="指定 SDL 渲染后端，例如 metal、opengl、software。">渲染驱动
                    <select id="renderDriver">
                      <option value="">默认</option>
                      <option value="metal">Metal</option>
                      <option value="opengl">OpenGL</option>
                      <option value="opengles2">OpenGLES2</option>
                      <option value="software">Software</option>
                    </select>
                    <span class="field-note">渲染异常时尝试。</span>
                  </label>
                  <label title="关闭控制台启动的 scrcpy 后同时关闭 adb server。" class="check"><input id="killAdbOnClose" type="checkbox">退出时关闭 ADB</label>
                  <label title="不清理设备上的临时服务文件。" class="check"><input id="noCleanup" type="checkbox">不清理临时文件</label>
                </div>
                <label title="仍可填写暂未做成控件的原始 scrcpy 参数，例如 --list-encoders。">额外 scrcpy 参数
                  <input id="extraArgs" placeholder="--print-fps" autocomplete="off">
                  <span class="field-note">高级兜底；多个参数用空格分隔。</span>
                </label>
              </details>
            </div>
          </details>
          <pre id="log" aria-live="polite"></pre>
        </div>
      </section>
    </div>

    <div class="workflow-column" aria-label="操控与文件传输">
      <section id="controlsPanel" class="controls-panel">
        <div class="section-header">
          <h2>操控</h2>
          <button id="wakeBtn" title="唤醒当前设备">唤醒</button>
        </div>
        <div id="controlsLocked" class="locked-message">启动 scrcpy 后显示控制按钮</div>
        <div id="controlsBody" class="body grid hidden">
          <div class="grid controls">
            <button class="control-button" data-key="back" title="Android 返回">返回<span>上一页</span></button>
            <button class="control-button" data-key="home" title="Android 主页">主页<span>回桌面</span></button>
            <button class="control-button" data-key="apps" title="Android 多任务">多任务<span>最近应用</span></button>
            <button class="control-button" data-key="volume_down" title="音量减">音量 -<span>调低</span></button>
            <button class="control-button" data-key="mute" title="静音">静音<span>切换</span></button>
            <button class="control-button" data-key="volume_up" title="音量加">音量 +<span>调高</span></button>
            <button class="control-button" data-statusbar="expand-notifications" title="展开通知栏">通知栏<span>展开</span></button>
            <button class="control-button" data-statusbar="expand-settings" title="展开快捷设置">快捷设置<span>展开</span></button>
            <button class="control-button" data-statusbar="collapse" title="收起系统面板">收起面板<span>关闭</span></button>
            <button class="control-button" data-key="power" title="电源键">电源<span>按一下</span></button>
            <button class="control-button" data-key="sleep" title="熄屏">熄屏<span>关闭屏幕</span></button>
            <button class="control-button" id="screenshotBtn" title="保存当前设备截图">截图<span>PNG 文件</span></button>
          </div>
          <div class="grid two">
            <label>输入文本
              <input id="textInput" placeholder="仅支持可靠 ASCII 文本" autocomplete="off">
            </label>
            <label>ADB 命令
              <input id="shellInput" placeholder="input keyevent 4" autocomplete="off">
            </label>
          </div>
          <div class="grid two">
            <button id="typeTextBtn" title="通过 adb shell input text 输入 ASCII 文本">输入文本</button>
            <button id="shellBtn" title="在当前设备上执行 adb shell 命令">执行命令</button>
          </div>
        </div>
      </section>

      <section id="transferPanel">
        <div class="section-header">
          <h2>文件传输</h2>
          <button id="openDownloadsBtn" title="打开本机下载目录；如果刚下载过文件会直接定位到它">打开下载目录</button>
        </div>
        <div id="transferLocked" class="locked-message">连接 ADB 设备后可上传或下载文件</div>
        <div id="transferBody" class="body grid hidden">
          <div class="transfer-heading">上传到手机</div>
          <div id="dropZone" class="drop-zone" tabindex="0" role="button" aria-label="选择或拖入要上传的文件">
            <input id="fileInput" type="file" multiple hidden>
            <strong id="dropZoneTitle">点击选择文件，或把文件拖到这里</strong>
            <span id="dropZoneDetail" class="field-note">APK 会直接安装，其它文件推送到下方目录；可一次选择多个文件。</span>
          </div>
          <div class="grid two">
            <label>手机目标目录
              <input id="uploadTarget" value="/sdcard/Download" placeholder="/sdcard/Download" autocomplete="off">
            </label>
            <label title="勾选后 .apk 文件通过 adb install -r 安装；取消勾选则和普通文件一样推送到目标目录。" class="check"><input id="installApk" type="checkbox" checked>APK 直接安装</label>
          </div>
          <div class="grid two">
            <button id="uploadBtn" class="primary" title="把已选择的文件上传到手机">上传到手机</button>
            <button id="clearFilesBtn" title="清空已选择的文件">清空选择</button>
          </div>
          <div class="transfer-heading">从手机下载</div>
          <label>手机路径
            <input id="remotePath" value="/sdcard/Download" placeholder="/sdcard/Download" autocomplete="off">
          </label>
          <div class="grid three">
            <button id="browseBtn" title="列出该目录的内容">浏览目录</button>
            <button id="parentDirBtn" title="返回上一级目录">上级目录</button>
            <button id="pullBtn" class="good" title="把该文件或整个目录下载到本机下载目录">下载到本机</button>
          </div>
          <div id="fileList" class="file-list hidden"></div>
          <div id="transferNote" class="field-note">下载会保存到本机的 ~/Downloads/scrcpy-control-panel 目录。</div>
        </div>
      </section>
    </div>
  </main>
  <script>
    const state = {
      devices: [],
      preferences: {},
      panelVersion: "",
      adb: false,
      scrcpy: false,
      scrcpyRunning: false,
      scrcpyKnown: false,
      scrcpyPid: null,
      scrcpyExitCode: null,
      scrcpyLogPath: "",
      wirelessConnected: false,
      manualDisconnect: "",
      nextAction: "refresh",
      busy: false
    };
    const $ = (id) => document.getElementById(id);
    let statusPromise = null;
    const startupOptionIds = [
      "maxSize", "bitRate", "maxFps", "videoCodec", "videoSource", "videoBuffer", "crop", "displayId", "captureOrientation",
      "noVideo", "noVideoPlayback", "noDownsizeOnError", "noMipmaps", "printFps",
      "noAudio", "noAudioPlayback", "audioDup", "requireAudio", "audioCodec", "audioSource", "audioBitRate", "audioBuffer", "audioOutputBuffer",
      "fullscreen", "alwaysOnTop", "windowBorderless", "windowTitle", "renderFit", "backgroundColor",
      "windowWidth", "windowHeight", "windowX", "windowY", "noWindowAspectLock", "noWindow",
      "turnScreenOff", "stayAwake", "keepActive", "powerOffOnClose", "noPowerOn", "disableScreensaver", "showTouches",
      "screenOffTimeout", "startApp",
      "keyboardMode", "mouseMode", "gamepadMode", "noControl", "preferText", "noClipboardAutosync", "noKeyRepeat", "rawKeyEvents", "noMouseHover", "legacyPaste",
      "recordFile", "recordFormat", "recordOrientation",
      "cameraId", "cameraFacing", "cameraSize", "cameraFps", "cameraZoom", "cameraAr", "cameraTorch", "cameraHighSpeed",
      "newDisplay", "flexDisplay", "noVdDestroyContent",
      "timeLimit", "pauseOnExit", "verbosity", "pushTarget", "renderDriver", "killAdbOnClose", "noCleanup", "extraArgs"
    ];
    const transferPreferenceIds = ["uploadTarget", "installApk", "remotePath"];
    const preferenceIds = ["wirelessAddress", ...startupOptionIds, ...transferPreferenceIds];
    const TRANSFER_TIMEOUT_MS = 20 * 60 * 1000;
    let transferFiles = [];
    let transferListPath = "";
    let transferSelectedFile = "";
    let lastDownloadPath = "";

    function log(message) {
      const now = new Date().toLocaleTimeString();
      const el = $("log");
      el.textContent = `[${now}] ${message}\n` + el.textContent;
    }

    function selectedSerial() {
      return $("serialSelect").value.trim();
    }

    function activeSerial() {
      const wireless = connectedWirelessDevice();
      return wireless ? wireless.serial : selectedSerial();
    }

    function selectedDevice() {
      const serial = selectedSerial();
      return state.devices.find((device) => device.serial === serial) || null;
    }

    function activeDevice() {
      const serial = activeSerial();
      return state.devices.find((device) => device.serial === serial) || null;
    }

    function isWirelessSerial(serial) {
      return /^.+:\d+$/.test(serial);
    }

    function isReadyDevice(device) {
      return !!device && device.state === "device";
    }

    function normalizedWirelessAddress() {
      const address = $("wirelessAddress").value.trim();
      if (!address) return "";
      return address.includes(":") ? address : `${address}:5555`;
    }

    function autoReconnectAddress() {
      const address = normalizedWirelessAddress();
      return address && address !== state.manualDisconnect ? address : "";
    }

    function logReconnect(results) {
      for (const item of results || []) log(item.message);
    }

    function connectedWirelessDevice() {
      const address = normalizedWirelessAddress();
      return state.devices.find((device) => {
        return device.state === "device" && isWirelessSerial(device.serial) && (!address || device.serial === address);
      }) || null;
    }

    function setDisabled(selector, disabled) {
      for (const el of document.querySelectorAll(selector)) {
        el.disabled = !!disabled;
      }
    }

    function setTargetButton(selector) {
      for (const el of document.querySelectorAll(".target-button")) {
        el.classList.remove("target-button");
      }
      if (selector) {
        const el = document.querySelector(selector);
        if (el) el.classList.add("target-button");
      }
    }

    function setStep(index, stateName) {
      const el = $(`step${index}`);
      el.classList.remove("active", "done");
      if (stateName) el.classList.add(stateName);
    }

    function updateWorkflow() {
      const device = selectedDevice();
      const serial = selectedSerial();
      const ready = isReadyDevice(device);
      const wireless = isWirelessSerial(serial);
      const usb = ready && !wireless;
      const address = normalizedWirelessAddress();
      const canUseAdb = state.adb;
      const connectedWireless = connectedWirelessDevice();
      const active = activeDevice();
      const activeReady = isReadyDevice(active);
      state.wirelessConnected = !!connectedWireless;
      const canStart = canUseAdb && state.scrcpy && activeReady;
      const canStop = state.scrcpyRunning;
      const canControl = canUseAdb && state.scrcpyRunning && activeReady;
      const busy = state.busy;
      const hasReadyDevice = state.devices.some(isReadyDevice);

      const connectBtn = $("connectBtn");
      connectBtn.textContent = state.wirelessConnected ? "断开连接" : "无线连接";
      connectBtn.title = state.wirelessConnected ? "断开当前无线 ADB 连接" : "连接无线 ADB 地址";
      connectBtn.className = state.wirelessConnected ? "danger solid wide" : "primary wide";
      const startBtn = $("startBtn");
      startBtn.textContent = state.scrcpyRunning ? "停止 scrcpy" : "启动 scrcpy";
      startBtn.title = state.scrcpyRunning ? "停止由本控制台启动的 scrcpy 进程" : "为当前设备启动 scrcpy";
      startBtn.className = state.scrcpyRunning ? "danger solid" : "good";
      setDisabled("#refreshBtn", busy);
      setDisabled("#connectBtn", busy || !canUseAdb || (!state.wirelessConnected && !address));
      setDisabled("#tcpipBtn", busy || !canUseAdb || !usb);
      setDisabled("#restartAdbBtn", busy || !canUseAdb);
      setDisabled("#copySerialBtn", busy || !serial);
      setDisabled("#startBtn", busy || (!canStop && !canStart));
      setDisabled("#wakeBtn", busy || !canControl);
      setDisabled("[data-key], [data-statusbar], #screenshotBtn", busy || !canControl);
      setDisabled("#typeTextBtn", busy || !canControl || !$("textInput").value);
      setDisabled("#shellBtn", busy || !canControl || !$("shellInput").value.trim());
      $("controlsPanel").classList.toggle("locked-panel", !canControl);
      $("controlsLocked").classList.toggle("hidden", canControl);
      $("controlsBody").classList.toggle("hidden", !canControl);

      const canTransfer = canUseAdb && activeReady;
      $("transferLocked").classList.toggle("hidden", canTransfer);
      $("transferBody").classList.toggle("hidden", !canTransfer);
      $("dropZone").classList.toggle("disabled", busy);
      setDisabled("#uploadBtn", busy || !canTransfer || !transferFiles.length);
      setDisabled("#clearFilesBtn", busy || !transferFiles.length);
      setDisabled("#browseBtn, #parentDirBtn", busy || !canTransfer);
      setDisabled("#pullBtn", busy || !canTransfer || !$("remotePath").value.trim());
      setDisabled("#openDownloadsBtn", busy);
      setDisabled(".file-row", busy);

      setStep(1, state.adb && state.scrcpy ? "done" : "active");
      setStep(2, activeReady ? "done" : state.adb ? "active" : "");
      setStep(3, state.scrcpyRunning ? "done" : activeReady ? "active" : "");
      setStep(4, canControl ? "active" : "");

      let hint = "";
      if (!state.adb || !state.scrcpy) {
        hint = "先安装并确认 adb、scrcpy 可用。";
      } else if (usb) {
        hint = "当前检测到 USB 设备：可以直接启动 scrcpy；需要无线时再点击“开启 TCP/IP”。";
      } else if (!activeReady) {
        hint = "请填写无线地址后点击“无线连接”，或先用 USB 连接手机并授权。";
      } else if (!state.scrcpyRunning) {
        hint = "设备已就绪：现在点击“启动 scrcpy”打开投屏窗口。";
      } else {
        hint = "scrcpy 已启动：主按钮已切换为停止，也可以使用右侧区域操控手机。";
      }
      $("actionHint").textContent = hint;

      let nextTitle = "下一步";
      let nextDetail = hint;
      let nextText = "刷新设备";
      let nextAction = "refresh";
      let nextTarget = "#refreshBtn";
      let nextDisabled = false;

      if (!state.adb || !state.scrcpy) {
        nextTitle = "环境未就绪";
        nextDetail = "需要先让 adb 和 scrcpy 出现在 PATH 里。";
        nextText = "重新检测";
        nextAction = "refresh";
        nextTarget = "#refreshBtn";
      } else if (usb) {
        nextTitle = "下一步：启动投屏";
        nextDetail = "检测到 USB 设备，可以先直接打开 scrcpy。";
        nextText = "启动 scrcpy";
        nextAction = "start";
        nextTarget = "#startBtn";
      } else if (!state.wirelessConnected && address) {
        nextTitle = "下一步：连接手机";
        nextDetail = `将连接 ${address}。`;
        nextText = "连接手机";
        nextAction = "connect";
        nextTarget = "#connectBtn";
      } else if (activeReady && !state.scrcpyRunning) {
        nextTitle = "下一步：启动投屏";
        nextDetail = "设备已就绪，可以打开 scrcpy 窗口。";
        nextText = "启动 scrcpy";
        nextAction = "start";
        nextTarget = "#startBtn";
      } else if (!state.wirelessConnected) {
        nextTitle = "下一步：连接手机";
        nextDetail = hasReadyDevice ? "请选择可用设备，或填入手机的 ip:5555。" : "请插入 USB 线并授权，或填入无线地址。";
        nextText = "刷新设备";
        nextAction = "refresh";
        nextTarget = hasReadyDevice ? "#wirelessAddress" : "#refreshBtn";
        nextDisabled = hasReadyDevice && !address;
      } else if (!state.scrcpyRunning) {
        nextTitle = "下一步：启动投屏";
        nextDetail = "设备已就绪，可以打开 scrcpy 窗口。";
        nextText = "启动 scrcpy";
        nextAction = "start";
        nextTarget = "#startBtn";
      } else {
        nextTitle = "已准备好";
        nextDetail = "投屏窗口已启动，绿色启动按钮已切换为红色停止按钮。";
        nextText = "查看操控按钮";
        nextAction = "controls";
        nextTarget = "#startBtn";
      }
      state.nextAction = nextAction;
      $("nextTitle").textContent = nextTitle;
      $("nextDetail").textContent = nextDetail;
      $("nextActionBtn").textContent = busy ? "处理中..." : nextText;
      setDisabled("#nextActionBtn", busy || nextDisabled);
      setTargetButton(nextTarget);
    }

    function savePreferences() {
      const data = {};
      for (const id of preferenceIds) {
        const el = $(id);
        data[id] = el.type === "checkbox" ? el.checked : el.value;
      }
      data.serial = selectedSerial();
      data.manualDisconnect = state.manualDisconnect;
      state.preferences = data;
      localStorage.setItem("scrcpyControlPanel", JSON.stringify(data));
    }

    function loadPreferences() {
      try {
        const data = JSON.parse(localStorage.getItem("scrcpyControlPanel") || "{}");
        for (const [id, value] of Object.entries(data)) {
          const el = $(id);
          if (!el) continue;
          if (el.type === "checkbox") el.checked = !!value;
          else el.value = value;
        }
        state.manualDisconnect = data.manualDisconnect || "";
        state.preferences = data;
        return data;
      } catch {
        return {};
      }
    }

    async function fetchJson(path, options = {}, timeoutMs = 15000) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      let res;
      let text = "";
      try {
        res = await fetch(path, { ...options, signal: controller.signal });
        text = await res.text();
      } catch (err) {
        if (err.name === "AbortError") {
          throw new Error("请求超时：ADB 可能暂时无响应，请稍后重试或点击“重启 ADB”。");
        }
        throw err;
      } finally {
        clearTimeout(timer);
      }
      let data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        throw new Error(text || res.statusText || "控制台返回了非 JSON 响应");
      }
      if (!res.ok || !data.ok) {
        const details = data.log_tail ? `\n\nscrcpy 最近日志：\n${data.log_tail}` : "";
        throw new Error((data.error || data.stderr || data.stdout || res.statusText) + details);
      }
      return data;
    }

    async function api(path, payload, timeoutMs = 25000) {
      return fetchJson(path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload || {})
      }, timeoutMs);
    }

    async function doStatus() {
      const data = await fetchJson("/api/status", {}, 12000);
      state.devices = data.devices || [];
      state.panelVersion = data.version || "未知";
      state.adb = !!data.adb;
      state.scrcpy = !!data.scrcpy;
      const wasRunning = state.scrcpyRunning;
      const wasKnown = state.scrcpyKnown;
      const process = data.scrcpy_process || {};
      state.scrcpyRunning = !!data.scrcpy_running;
      state.scrcpyKnown = true;
      state.scrcpyPid = process.pid || null;
      state.scrcpyExitCode = process.exit_code ?? null;
      state.scrcpyLogPath = process.log_path || "";
      $("panelVersion").textContent = `v${state.panelVersion}`;
      document.title = `scrcpy 控制台 v${state.panelVersion}`;
      $("adbStatus").textContent = data.adb ? "adb 就绪" : "缺少 adb";
      $("adbStatus").className = `pill ${data.adb ? "ok" : "bad"}`;
      $("scrcpyStatus").textContent = data.scrcpy ? "scrcpy 就绪" : "缺少 scrcpy";
      $("scrcpyStatus").className = `pill ${data.scrcpy ? "ok" : "bad"}`;
      if (state.scrcpyRunning) {
        $("processStatus").textContent = state.scrcpyPid ? `scrcpy 运行中 #${state.scrcpyPid}` : "scrcpy 运行中";
      } else if (state.scrcpyExitCode !== null) {
        $("processStatus").textContent = `scrcpy 已关闭 (${state.scrcpyExitCode})`;
      } else {
        $("processStatus").textContent = "scrcpy 未启动";
      }
      $("processStatus").className = `pill ${data.scrcpy_running ? "ok" : ""}`;
      if (wasKnown && wasRunning && !state.scrcpyRunning) {
        const suffix = state.scrcpyExitCode !== null ? `，退出码 ${state.scrcpyExitCode}` : "";
        log(`scrcpy 窗口已关闭${suffix}`);
        if (process.log_path) log(`scrcpy 日志：${process.log_path}`);
        if (process.log_tail) log(`scrcpy 最近日志：\n${process.log_tail}`);
      }
      renderDevices();
      updateWorkflow();
      return data;
    }

    async function status({ force = false } = {}) {
      if (statusPromise && !force) return statusPromise;
      if (statusPromise && force) {
        try { await statusPromise; } catch {}
      }
      const promise = doStatus();
      statusPromise = promise;
      try {
        return await promise;
      } finally {
        if (statusPromise === promise) statusPromise = null;
      }
    }

    function renderDevices() {
      const select = $("serialSelect");
      const previous = select.value || state.preferences.serial || "";
      select.innerHTML = "";
      for (const device of state.devices) {
        const option = document.createElement("option");
        option.value = device.serial;
        option.textContent = `${device.serial} (${device.state}${device.model ? ", " + device.model : ""})`;
        select.appendChild(option);
      }
      if (!state.devices.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "没有 ADB 设备";
        select.appendChild(option);
      }
      if ([...select.options].some((option) => option.value === previous)) {
        select.value = previous;
      }
      if (isWirelessSerial(select.value) && !$("wirelessAddress").value.trim()) {
        $("wirelessAddress").value = select.value;
      }
      const connected = connectedWirelessDevice();
      if (connected) {
        select.value = connected.serial;
      }
      const usable = state.devices.filter((device) => device.state === "device");
      $("deviceSummary").textContent = `${usable.length} 台可用 / 共 ${state.devices.length} 台`;
      savePreferences();
      updateWorkflow();
    }

    function startPayload() {
      savePreferences();
      const payload = {
        serial: activeSerial(),
      };
      for (const id of startupOptionIds) {
        const el = $(id);
        payload[id] = el.type === "checkbox" ? el.checked : el.value;
      }
      return payload;
    }

    async function runKey(key) {
      const data = await api("/api/keyevent", { serial: activeSerial(), key });
      log(data.command);
    }

    async function runStatusbar(action) {
      const data = await api("/api/statusbar", { serial: activeSerial(), action });
      log(data.command);
    }

    async function runUiAction(action) {
      if (state.busy) return;
      state.busy = true;
      updateWorkflow();
      try {
        await action();
      } catch (err) {
        log(`错误：${err.message}`);
      } finally {
        state.busy = false;
        updateWorkflow();
      }
    }

    async function reconnectSavedWireless() {
      const data = await api("/api/refresh", { address: autoReconnectAddress() });
      logReconnect(data.reconnect);
      await status({ force: true });
    }

    async function refreshDevices() {
      await reconnectSavedWireless();
      log("已刷新设备");
    }

    async function toggleWirelessConnection() {
      const disconnecting = state.wirelessConnected;
      const address = disconnecting ? normalizedWirelessAddress() || activeSerial() : normalizedWirelessAddress();
      const data = disconnecting
        ? await api("/api/disconnect", { address })
        : await api("/api/connect", { address });
      // 手动断开的地址不在刷新和重启 ADB 时自动连回，直到再次手动连接。
      state.manualDisconnect = disconnecting ? address : "";
      savePreferences();
      log(data.stdout || data.command);
      await status({ force: true });
    }

    async function enableTcpip() {
      const data = await api("/api/tcpip", { serial: selectedSerial(), port: 5555 });
      if (data.address) $("wirelessAddress").value = data.address;
      log(data.stdout || data.command);
      await status({ force: true });
    }

    async function startScrcpy() {
      const payload = startPayload();
      const data = await api("/api/start", payload);
      if (payload.serial && payload.serial === state.manualDisconnect) {
        state.manualDisconnect = "";
        savePreferences();
      }
      logReconnect(data.reconnect);
      log(data.command);
      if (data.log_path) log(`scrcpy 日志：${data.log_path}`);
      await status({ force: true });
    }

    async function stopScrcpy() {
      const data = await api("/api/stop");
      log(data.message || "已停止");
      await status({ force: true });
    }

    async function toggleScrcpy() {
      if (state.scrcpyRunning) {
        await stopScrcpy();
      } else {
        await startScrcpy();
      }
    }

    async function restartAdb() {
      const data = await api("/api/restart-adb", { address: autoReconnectAddress() }, 60000);
      log(data.stdout || data.stderr || data.command || "ADB 已重启");
      logReconnect(data.reconnect);
      await status({ force: true });
    }

    async function shutdownPanel() {
      if (!confirm("关闭控制台会停止由控制台启动的 scrcpy，并关闭本地网页服务。继续吗？")) {
        return;
      }
      const data = await api("/api/shutdown");
      log(data.message || "控制台已关闭");
      $("actionHint").textContent = "控制台服务已关闭。需要再次使用时，请重新打开 Scrcpy Control Panel.app。";
      setDisabled("button", true);
    }

    async function runNextAction() {
      if (state.nextAction === "refresh") {
        await refreshDevices();
      } else if (state.nextAction === "connect") {
        await toggleWirelessConnection();
      } else if (state.nextAction === "tcpip") {
        await enableTcpip();
      } else if (state.nextAction === "start") {
        await startScrcpy();
      } else if (state.nextAction === "controls") {
        $("controlsPanel").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }

    function formatSize(bytes) {
      if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return "";
      const units = ["B", "KB", "MB", "GB", "TB"];
      let value = Number(bytes);
      let index = 0;
      while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index += 1;
      }
      return `${index === 0 ? value : value.toFixed(value >= 100 ? 0 : 1)} ${units[index]}`;
    }

    function parentPath(path) {
      const trimmed = (path || "/").trim().replace(/\/+$/, "");
      const index = trimmed.lastIndexOf("/");
      return index <= 0 ? "/" : trimmed.slice(0, index);
    }

    function addFiles(list) {
      for (const file of list || []) {
        const duplicate = transferFiles.some((item) =>
          item.name === file.name && item.size === file.size && item.lastModified === file.lastModified);
        if (!duplicate) transferFiles.push(file);
      }
      renderSelectedFiles();
    }

    function renderSelectedFiles() {
      if (!transferFiles.length) {
        $("dropZoneTitle").textContent = "点击选择文件，或把文件拖到这里";
        $("dropZoneDetail").textContent = "APK 会直接安装，其它文件推送到下方目录；可一次选择多个文件。";
      } else {
        const total = transferFiles.reduce((sum, file) => sum + file.size, 0);
        $("dropZoneTitle").textContent = `已选择 ${transferFiles.length} 个文件，共 ${formatSize(total)}`;
        $("dropZoneDetail").textContent = transferFiles.map((file) => file.name).join("、");
      }
      updateWorkflow();
    }

    async function uploadFile(file) {
      const params = new URLSearchParams({
        serial: activeSerial(),
        name: file.name,
        target: $("uploadTarget").value.trim(),
        install: $("installApk").checked ? "1" : "0"
      });
      return fetchJson(`/api/upload?${params.toString()}`, {
        method: "POST",
        headers: { "content-type": "application/octet-stream" },
        body: file
      }, TRANSFER_TIMEOUT_MS);
    }

    async function uploadSelectedFiles() {
      const target = $("uploadTarget").value.trim();
      if (target && !target.startsWith("/")) {
        throw new Error("手机目标目录必须是以 / 开头的绝对路径");
      }
      for (const file of [...transferFiles]) {
        log(`正在上传 ${file.name}（${formatSize(file.size)}）...`);
        const data = await uploadFile(file);
        transferFiles = transferFiles.filter((item) => item !== file);
        renderSelectedFiles();
        if (data.action === "install") {
          log(`已安装 ${file.name}：${data.summary || "Success"}`);
          $("transferNote").textContent = `已安装 ${file.name}`;
        } else {
          log(`已推送到 ${data.remote}${data.summary ? "（" + data.summary + "）" : ""}`);
          $("transferNote").textContent = `已推送到 ${data.remote}`;
        }
      }
    }

    async function browseRemote(path) {
      const data = await api("/api/ls", { serial: activeSerial(), path });
      transferListPath = data.path;
      transferSelectedFile = "";
      $("remotePath").value = data.path;
      savePreferences();
      renderFileList(data);
      if (data.warning) log(`列出目录时有警告：${data.warning}`);
    }

    function renderFileList(data) {
      const box = $("fileList");
      box.innerHTML = "";
      box.classList.remove("hidden");
      const header = document.createElement("div");
      header.className = "file-list-header";
      header.textContent = `${data.path} · ${data.entries.length} 项 · 点击目录进入，点击文件选中，双击文件直接下载`;
      box.appendChild(header);
      if (!data.entries.length) {
        const empty = document.createElement("div");
        empty.className = "file-list-empty";
        empty.textContent = "空目录";
        box.appendChild(empty);
      }
      const kinds = { dir: "目录", file: "文件", link: "链接", other: "其它" };
      for (const entry of data.entries) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = `file-row ${entry.type}`;
        row.dataset.path = entry.path;
        row.dataset.type = entry.type;
        row.title = entry.modified ? `${entry.path}\n${entry.modified}` : entry.path;
        const kind = document.createElement("span");
        kind.className = "kind";
        kind.textContent = kinds[entry.type] || kinds.other;
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = entry.name + (entry.type === "dir" ? "/" : "");
        const size = document.createElement("span");
        size.className = "size";
        size.textContent = entry.type === "dir" ? "" : formatSize(entry.size);
        row.append(kind, name, size);
        box.appendChild(row);
      }
      updateWorkflow();
    }

    function selectRemoteEntry(row) {
      for (const el of $("fileList").querySelectorAll(".file-row.selected")) {
        el.classList.remove("selected");
      }
      row.classList.add("selected");
      transferSelectedFile = row.dataset.path;
      $("remotePath").value = row.dataset.path;
      savePreferences();
      updateWorkflow();
    }

    async function browseCurrentPath() {
      const value = $("remotePath").value.trim();
      const target = transferSelectedFile && value === transferSelectedFile ? parentPath(value) : value;
      await browseRemote(target);
    }

    async function browseParentPath() {
      const value = $("remotePath").value.trim();
      await browseRemote(parentPath(transferListPath || value));
    }

    async function pullRemote() {
      const path = $("remotePath").value.trim();
      log(`正在下载 ${path} ...`);
      const data = await api("/api/pull", { serial: activeSerial(), path }, TRANSFER_TIMEOUT_MS);
      lastDownloadPath = data.path;
      log(`已下载到 ${data.path}${data.summary ? "（" + data.summary + "）" : ""}`);
      $("transferNote").textContent = `已下载到 ${data.path}`;
    }

    async function openDownloads() {
      const data = await api("/api/open-downloads", { path: lastDownloadPath });
      log(`已打开 ${data.path}`);
    }

    document.addEventListener("click", async (event) => {
      const target = event.target.closest("button");
      if (!target) return;
      if (target.disabled) return;
      if (!target.dataset.key && !target.dataset.statusbar) return;
      await runUiAction(async () => {
        if (target.dataset.key) {
          await runKey(target.dataset.key);
        } else if (target.dataset.statusbar) {
          await runStatusbar(target.dataset.statusbar);
        }
      });
    });

    $("nextActionBtn").addEventListener("click", () => runUiAction(runNextAction));
    $("refreshBtn").addEventListener("click", () => runUiAction(refreshDevices));
    $("connectBtn").addEventListener("click", () => runUiAction(toggleWirelessConnection));
    $("tcpipBtn").addEventListener("click", () => runUiAction(enableTcpip));
    $("restartAdbBtn").addEventListener("click", () => runUiAction(restartAdb));
    $("shutdownPanelBtn").addEventListener("click", () => runUiAction(shutdownPanel));
    $("copySerialBtn").addEventListener("click", async () => {
      await navigator.clipboard.writeText(selectedSerial());
      log("已复制序列号");
    });
    $("startBtn").addEventListener("click", () => runUiAction(toggleScrcpy));
    $("wakeBtn").addEventListener("click", () => runUiAction(async () => runKey("wake")));
    $("screenshotBtn").addEventListener("click", () => runUiAction(async () => {
      const data = await api("/api/screenshot", { serial: activeSerial() });
      log(`截图已保存：${data.path}`);
    }));
    $("typeTextBtn").addEventListener("click", () => runUiAction(async () => {
      const data = await api("/api/type-text", { serial: activeSerial(), text: $("textInput").value });
      log(data.command);
    }));
    $("shellBtn").addEventListener("click", () => runUiAction(async () => {
      const data = await api("/api/shell", { serial: activeSerial(), command: $("shellInput").value });
      log(data.stdout || data.command);
    }));
    const dropZone = $("dropZone");
    dropZone.addEventListener("click", () => {
      if (!state.busy) $("fileInput").click();
    });
    dropZone.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      if (!state.busy) $("fileInput").click();
    });
    dropZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropZone.classList.add("dragging");
    });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
    dropZone.addEventListener("drop", (event) => {
      event.preventDefault();
      dropZone.classList.remove("dragging");
      if (!state.busy) addFiles(event.dataTransfer.files);
    });
    document.addEventListener("dragover", (event) => event.preventDefault());
    document.addEventListener("drop", (event) => event.preventDefault());
    $("fileInput").addEventListener("change", () => {
      addFiles($("fileInput").files);
      $("fileInput").value = "";
    });
    $("uploadBtn").addEventListener("click", () => runUiAction(uploadSelectedFiles));
    $("clearFilesBtn").addEventListener("click", () => {
      transferFiles = [];
      renderSelectedFiles();
    });
    $("browseBtn").addEventListener("click", () => runUiAction(browseCurrentPath));
    $("parentDirBtn").addEventListener("click", () => runUiAction(browseParentPath));
    $("pullBtn").addEventListener("click", () => runUiAction(pullRemote));
    $("openDownloadsBtn").addEventListener("click", () => runUiAction(openDownloads));
    $("fileList").addEventListener("click", (event) => {
      const row = event.target.closest(".file-row");
      if (!row || row.disabled || state.busy) return;
      if (row.dataset.type === "dir") {
        runUiAction(async () => browseRemote(row.dataset.path));
      } else {
        selectRemoteEntry(row);
      }
    });
    $("fileList").addEventListener("dblclick", (event) => {
      const row = event.target.closest(".file-row");
      if (!row || row.disabled || state.busy || row.dataset.type === "dir") return;
      selectRemoteEntry(row);
      runUiAction(pullRemote);
    });
    $("uploadTarget").addEventListener("input", updateWorkflow);
    $("remotePath").addEventListener("input", () => {
      if ($("remotePath").value.trim() !== transferSelectedFile) transferSelectedFile = "";
      updateWorkflow();
    });
    $("serialSelect").addEventListener("change", () => {
      if (isWirelessSerial(selectedSerial())) {
        $("wirelessAddress").value = selectedSerial();
      }
      savePreferences();
      updateWorkflow();
    });
    for (const id of preferenceIds) {
      $(id).addEventListener("change", () => {
        savePreferences();
        updateWorkflow();
      });
    }
    $("textInput").addEventListener("input", updateWorkflow);
    $("shellInput").addEventListener("input", updateWorkflow);

    loadPreferences();
    updateWorkflow();
    status()
      .then(() => {
        if (autoReconnectAddress() && !connectedWirelessDevice()) return runUiAction(reconnectSavedWireless);
      })
      .catch((err) => log(`错误：${err.message}`));
    setInterval(() => {
      if (state.busy || document.hidden || statusPromise) return;
      status().catch(() => {});
    }, 2500);
  </script>
</body>
</html>
"""


@dataclass
class CommandResult:
    ok: bool
    command: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "stdout": self.stdout.strip(),
            "stderr": self.stderr.strip(),
            "returncode": self.returncode,
        }


class PanelState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.scrcpy_process: subprocess.Popen[str] | None = None
        self.scrcpy_command: list[str] | None = None
        self.scrcpy_log_path: Path | None = None
        self.last_scrcpy_pid: int | None = None
        self.last_scrcpy_command: list[str] | None = None
        self.last_scrcpy_exit_code: int | None = None
        self.last_scrcpy_exit_at: float | None = None
        self.last_scrcpy_log_path: Path | None = None
        self.last_scrcpy_log_tail = ""

    def _remember_exit_locked(self, process: subprocess.Popen[str], returncode: int) -> None:
        self.last_scrcpy_pid = process.pid
        self.last_scrcpy_command = self.scrcpy_command
        self.last_scrcpy_exit_code = returncode
        self.last_scrcpy_exit_at = time.time()
        self.last_scrcpy_log_path = self.scrcpy_log_path
        self.last_scrcpy_log_tail = tail_file(self.scrcpy_log_path)
        self.scrcpy_process = None
        self.scrcpy_command = None
        self.scrcpy_log_path = None

    def is_scrcpy_running(self) -> bool:
        with self.lock:
            if self.scrcpy_process is None:
                return False
            returncode = self.scrcpy_process.poll()
            if returncode is None:
                return True
            self._remember_exit_locked(self.scrcpy_process, returncode)
            return False

    def scrcpy_status(self) -> dict[str, Any]:
        with self.lock:
            process = self.scrcpy_process
            command = self.scrcpy_command
            if process is not None:
                returncode = process.poll()
                if returncode is None:
                    return {
                        "running": True,
                        "pid": process.pid,
                        "command": command_text(command) if command else "",
                        "exit_code": None,
                        "exit_at": None,
                        "log_path": str(self.scrcpy_log_path) if self.scrcpy_log_path else "",
                        "log_tail": "",
                    }
                self._remember_exit_locked(process, returncode)

            return {
                "running": False,
                "pid": self.last_scrcpy_pid,
                "command": command_text(self.last_scrcpy_command) if self.last_scrcpy_command else "",
                "exit_code": self.last_scrcpy_exit_code,
                "exit_at": self.last_scrcpy_exit_at,
                "log_path": str(self.last_scrcpy_log_path) if self.last_scrcpy_log_path else "",
                "log_tail": self.last_scrcpy_log_tail,
            }

    def set_scrcpy(self, process: subprocess.Popen[str], command: list[str], log_path: Path) -> None:
        with self.lock:
            self.scrcpy_process = process
            self.scrcpy_command = command
            self.scrcpy_log_path = log_path
            self.last_scrcpy_pid = None
            self.last_scrcpy_command = None
            self.last_scrcpy_exit_code = None
            self.last_scrcpy_exit_at = None
            self.last_scrcpy_log_path = None
            self.last_scrcpy_log_tail = ""

    def stop_scrcpy(self) -> str:
        with self.lock:
            process = self.scrcpy_process
            if process is None:
                return "当前没有由控制台启动的 scrcpy 进程"
            returncode = process.poll()
            if returncode is not None:
                self._remember_exit_locked(process, returncode)
                return "scrcpy 已经停止"

            try:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                self._remember_exit_locked(process, process.poll() or 0)
                return "scrcpy 已经停止"

        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=4)

        with self.lock:
            self._remember_exit_locked(process, process.returncode)
        return "scrcpy 已停止"


STATE = PanelState()


def executable(name: str) -> str | None:
    return shutil.which(name)


def command_text(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip() or "device")
    return name.strip("._-") or "device"


def tail_file(path: Path | None, limit: int = SCRCPY_LOG_TAIL_BYTES) -> str:
    if path is None:
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size > limit:
                stream.seek(-limit, os.SEEK_END)
            data = stream.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace").strip()
    if size > limit:
        text = "...\n" + text
    return text


def is_adb_command(args: list[str]) -> bool:
    if not args:
        return False
    return Path(args[0]).name == "adb"


def run(args: list[str], timeout: int = 20) -> CommandResult:
    lock_acquired = False
    try:
        if is_adb_command(args):
            ADB_COMMAND_LOCK.acquire()
            lock_acquired = True
        completed = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return CommandResult(False, command_text(args), stderr=f"找不到命令：{args[0]}", returncode=127)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(False, command_text(args), stdout=stdout, stderr=stderr or "命令执行超时", returncode=124)
    finally:
        if lock_acquired:
            ADB_COMMAND_LOCK.release()

    return CommandResult(
        completed.returncode == 0,
        command_text(args),
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )


def require_adb() -> str:
    adb = executable("adb")
    if not adb:
        raise RuntimeError("PATH 中找不到 adb")
    return adb


def require_scrcpy() -> str:
    scrcpy = executable("scrcpy")
    if not scrcpy:
        raise RuntimeError("PATH 中找不到 scrcpy")
    return scrcpy


def parse_devices(output: str) -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        details = " ".join(parts[2:])
        model = ""
        product = ""
        device = ""
        for item in parts[2:]:
            if item.startswith("model:"):
                model = item.split(":", 1)[1]
            elif item.startswith("product:"):
                product = item.split(":", 1)[1]
            elif item.startswith("device:"):
                device = item.split(":", 1)[1]
        devices.append(
            {
                "serial": serial,
                "state": state,
                "model": model,
                "product": product,
                "device": device,
                "details": details,
            }
        )
    return devices


def get_adb_devices() -> tuple[list[dict[str, str]], CommandResult]:
    adb = require_adb()
    result = run([adb, "devices", "-l"], timeout=8)
    devices = parse_devices(result.stdout) if result.ok else []
    return devices, result


def find_device(devices: list[dict[str, str]], serial: str) -> dict[str, str] | None:
    return next((device for device in devices if device["serial"] == serial), None)


def wait_for_adb_device(
    serial: str,
    timeout: float = ADB_CONNECT_WAIT_TIMEOUT,
    poll_interval: float = ADB_CONNECT_POLL_INTERVAL,
) -> tuple[dict[str, str] | None, list[dict[str, str]], CommandResult]:
    deadline = time.monotonic() + timeout
    while True:
        devices, result = get_adb_devices()
        device = find_device(devices, serial)
        if device and device["state"] == "device":
            return device, devices, result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return device, devices, result
        time.sleep(min(poll_interval, remaining))


def is_wireless_serial(serial: str) -> bool:
    return bool(re.fullmatch(r".+:\d+", serial))


def reconnect_failure_hint(output: str) -> str:
    if "refused" in output.lower():
        return "手机可能重启过，无线 ADB 端口已关闭，请用 USB 连接后点击开启 TCP/IP（adb tcpip 5555）。"
    return "请确认手机和电脑在同一网络、手机未休眠，并且地址和端口正确。"


def reconnect_wireless(
    addresses: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], CommandResult]:
    """对不在设备列表中或已离线的无线地址补一次 adb connect。

    刷新列表和重启 ADB server 都不会主动连接无线设备，这里补上这一步。
    已可用的地址直接跳过；未授权的地址需要在手机上确认，不重复连接。
    """
    adb = require_adb()
    results: list[dict[str, Any]] = []
    with ADB_COMMAND_LOCK:
        devices, devices_result = get_adb_devices()
        for address in dict.fromkeys(addresses):
            device = find_device(devices, address)
            state = device["state"] if device else ""
            if state == "device":
                continue
            if state in ("unauthorized", "authorizing"):
                results.append(
                    {
                        "address": address,
                        "ok": False,
                        "state": state,
                        "message": f"{address} 等待授权，请在手机上允许 USB 调试。",
                    }
                )
                continue
            if device:
                # offline 的旧连接不会自行恢复，先断开再重连。
                run([adb, "disconnect", address], timeout=8)
            connect = run([adb, "connect", address], timeout=12)
            output = "\n".join(filter(None, [connect.stdout.strip(), connect.stderr.strip()]))
            wait = ADB_RECONNECT_WAIT_TIMEOUT if connect.ok and "connected to" in output else 0
            device, devices, devices_result = wait_for_adb_device(address, timeout=wait)
            ok = bool(device and device["state"] == "device")
            if ok:
                message = f"已自动连接 {address}"
            else:
                reason = output or (device["state"] if device else "未出现在设备列表")
                message = f"自动连接 {address} 失败：{reason}。{reconnect_failure_hint(output)}"
            results.append(
                {
                    "address": address,
                    "ok": ok,
                    "state": device["state"] if device else "",
                    "command": connect.command,
                    "message": message,
                }
            )
    return results, devices, devices_result


def adb_prefix(serial: str | None) -> list[str]:
    adb = require_adb()
    if serial:
        return [adb, "-s", serial]
    return [adb]


def infer_device_ip(serial: str) -> str | None:
    result = run(adb_prefix(serial) + ["shell", "ip", "route"], timeout=8)
    if not result.ok:
        return None
    match = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", result.stdout)
    if match:
        return match.group(1)
    for token in result.stdout.split():
        if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", token):
            if not token.endswith(".0") and token != "0.0.0.0":
                return token
    return None


def normalize_address(address: str) -> str:
    address = address.strip()
    if not address:
        raise RuntimeError("无线地址不能为空")
    if ":" not in address:
        address = f"{address}:5555"
    return address


def safe_shell_command(command: str) -> list[str]:
    command = command.strip()
    if not command:
        raise RuntimeError("ADB shell 命令不能为空")
    return shlex.split(command)


def encode_adb_input_text(text: str) -> str:
    if any(ord(char) > 127 for char in text):
        raise RuntimeError("adb shell input text 只能可靠输入纯 ASCII 文本")
    text = text.replace("%", "%25")
    text = text.replace(" ", "%s")
    for char in ['"', "'", "\\", "(", ")", "<", ">", "&", "|", ";"]:
        text = text.replace(char, "\\" + char)
    return text

LS_LINE_RE = re.compile(
    r"^(?P<perms>[-dlcbsp][rwxsStT-]{9})[+.@]?\s+"
    r"(?:\d+\s+)?"
    r"(?P<user>\S+)\s+(?P<group>\S+)\s+"
    r"(?:(?P<size>\d+)\s+)?"
    r"(?:\d+,\s*\d+\s+)?"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}(?::\d{2})?)\s+"
    r"(?P<name>.+)$"
)
ENTRY_TYPES = {"d": "dir", "-": "file", "l": "link"}


def normalize_device_path(path: str, default: str | None = DEFAULT_DEVICE_DIR) -> str:
    path = (path or "").strip()
    if not path:
        if default is None:
            raise RuntimeError("手机路径不能为空")
        path = default
    if any(char in path for char in ("\n", "\r", "\0")):
        raise RuntimeError("手机路径包含非法字符")
    if not path.startswith("/"):
        raise RuntimeError("手机路径必须是以 / 开头的绝对路径")
    normalized = posixpath.normpath(re.sub(r"/+", "/", path))
    return normalized if normalized.startswith("/") else "/" + normalized


def parse_ls_output(output: str, base: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in output.splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.startswith("total "):
            continue
        match = LS_LINE_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        kind = ENTRY_TYPES.get(match.group("perms")[0], "other")
        if kind == "link" and " -> " in name:
            name = name.split(" -> ", 1)[0]
        if name in (".", ".."):
            continue
        size = match.group("size")
        entries.append(
            {
                "name": name,
                "path": posixpath.join(base, name),
                "type": kind,
                "size": int(size) if size is not None and kind != "dir" else None,
                "modified": f"{match.group('date')} {match.group('time')}",
            }
        )
    entries.sort(key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))
    return entries


def list_device_dir(serial: str, path: str) -> dict[str, Any]:
    shell = f"cd {shlex.quote(path)} && ls -laL"
    result = run(adb_prefix(serial) + ["shell", shell], timeout=20)
    entries = parse_ls_output(result.stdout, path)
    if not result.ok and not entries:
        raise RuntimeError((result.stderr or result.stdout).strip() or "无法列出目录")
    return {
        "path": path,
        "parent": posixpath.dirname(path) or "/",
        "entries": entries,
        "command": result.command,
        "warning": result.stderr.strip() if not result.ok else "",
    }


def unique_local_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = os.path.splitext(name)
    for index in range(2, 1000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("下载目录中同名文件过多")


def safe_upload_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1].strip()
    if not name or name in (".", "..") or "\0" in name:
        raise RuntimeError("文件名无效")
    return name


def open_in_file_manager(path: Path, reveal: bool = False) -> list[str]:
    if sys.platform == "darwin":
        args = ["open", "-R", str(path)] if reveal else ["open", str(path)]
    elif os.name == "nt":
        args = ["explorer", f"/select,{path}"] if reveal else ["explorer", str(path)]
    else:
        args = ["xdg-open", str(path.parent if reveal else path)]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return args


def payload_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def payload_bool(payload: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        if key in payload:
            return bool(payload.get(key))
    return False


def add_value_arg(args: list[str], payload: dict[str, Any], option: str, *keys: str) -> None:
    value = payload_text(payload, *keys)
    if value:
        args.append(f"{option}={value}")


def add_flag_arg(args: list[str], payload: dict[str, Any], option: str, *keys: str) -> None:
    if payload_bool(payload, *keys):
        args.append(option)


def json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        return


def text_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, body: str, content_type: str) -> None:
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", content_type)
    handler.send_header("content-length", str(len(encoded)))
    handler.end_headers()
    try:
        handler.wfile.write(encoded)
    except (BrokenPipeError, ConnectionResetError):
        return


class ControlPanelServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = f"ScrcpyControlPanel/{CONTROL_PANEL_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.command == "GET" and urlparse(self.path).path == "/api/status":
            return
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                text_response(self, HTTPStatus.OK, HTML, "text/html; charset=utf-8")
                return
            if path == "/api/status":
                self.handle_status()
                return
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/upload":
                self.handle_upload()
                return
            payload = self.read_json()
            routes = {
                "/api/refresh": self.handle_refresh,
                "/api/connect": self.handle_connect,
                "/api/disconnect": self.handle_disconnect,
                "/api/tcpip": self.handle_tcpip,
                "/api/start": self.handle_start,
                "/api/stop": self.handle_stop,
                "/api/shutdown": self.handle_shutdown,
                "/api/restart-adb": self.handle_restart_adb,
                "/api/keyevent": self.handle_keyevent,
                "/api/statusbar": self.handle_statusbar,
                "/api/screenshot": self.handle_screenshot,
                "/api/type-text": self.handle_type_text,
                "/api/shell": self.handle_shell,
                "/api/ls": self.handle_ls,
                "/api/pull": self.handle_pull,
                "/api/open-downloads": self.handle_open_downloads,
            }
            route = routes.get(path)
            if route is None:
                json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
                return
            route(payload)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if not length:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def handle_status(self) -> None:
        adb = executable("adb")
        scrcpy = executable("scrcpy")
        scrcpy_status = STATE.scrcpy_status()
        devices: list[dict[str, str]] = []
        adb_error = ""
        if adb:
            result = run([adb, "devices", "-l"], timeout=8)
            if result.ok:
                devices = parse_devices(result.stdout)
            else:
                adb_error = result.stderr or result.stdout
        json_response(
            self,
            HTTPStatus.OK,
            {
                "ok": True,
                "version": CONTROL_PANEL_VERSION,
                "adb": bool(adb),
                "adb_path": adb,
                "adb_error": adb_error.strip(),
                "scrcpy": bool(scrcpy),
                "scrcpy_path": scrcpy,
                "scrcpy_running": scrcpy_status["running"],
                "scrcpy_process": scrcpy_status,
                "devices": devices,
                "download_dir": str(DOWNLOAD_DIR),
                "device_dir": DEFAULT_DEVICE_DIR,
            },
        )

    def handle_refresh(self, payload: dict[str, Any]) -> None:
        address = payload_text(payload, "address")
        addresses = [normalize_address(address)] if address else []
        reconnect, devices, result = reconnect_wireless(addresses)
        if not result.ok:
            response = result.to_json()
            response["reconnect"] = reconnect
            json_response(self, HTTPStatus.BAD_REQUEST, response)
            return
        json_response(self, HTTPStatus.OK, {"ok": True, "devices": devices, "reconnect": reconnect})

    def handle_connect(self, payload: dict[str, Any]) -> None:
        address = normalize_address(str(payload.get("address", "")))
        adb = require_adb()
        initial_connect_args = [adb, "connect", address]
        kill_args = [adb, "kill-server"]
        retry_connect_args = [adb, "connect", address]
        with ADB_COMMAND_LOCK:
            initial_result = run(initial_connect_args, timeout=12)
            initial_device = None
            initial_devices: list[dict[str, str]] = []
            initial_devices_result: CommandResult | None = None
            if initial_result.ok:
                initial_device, initial_devices, initial_devices_result = wait_for_adb_device(
                    address, timeout=ADB_CONNECT_INITIAL_WAIT_TIMEOUT
                )
                if initial_device and initial_device["state"] == "device":
                    response = initial_result.to_json()
                    response["devices"] = initial_devices
                    response["recovery_attempted"] = False
                    json_response(self, HTTPStatus.OK, response)
                    return

            kill_result = run(kill_args, timeout=8)
            if not kill_result.ok:
                response = kill_result.to_json()
                response["command"] = f"{command_text(initial_connect_args)} ; {command_text(kill_args)}"
                response["stdout"] = "\n".join(
                    filter(None, [initial_result.stdout.strip(), kill_result.stdout.strip()])
                )
                response["stderr"] = "\n".join(
                    filter(None, [initial_result.stderr.strip(), kill_result.stderr.strip()])
                )
                response["error"] = "首次无线连接不可用，随后重启 ADB server 失败。"
                response["devices"] = initial_devices
                response["recovery_attempted"] = True
                json_response(self, HTTPStatus.BAD_REQUEST, response)
                return

            retry_result = run(retry_connect_args, timeout=12)
            response = retry_result.to_json()
            response["command"] = (
                f"{command_text(initial_connect_args)} ; "
                f"{command_text(kill_args)} && {command_text(retry_connect_args)}"
            )
            response["stdout"] = "\n".join(
                filter(None, [initial_result.stdout.strip(), kill_result.stdout.strip(), retry_result.stdout.strip()])
            )
            response["stderr"] = "\n".join(
                filter(None, [initial_result.stderr.strip(), kill_result.stderr.strip(), retry_result.stderr.strip()])
            )
            response["recovery_attempted"] = True
            if retry_result.ok:
                device, devices, devices_result = wait_for_adb_device(address)
                response["devices"] = devices
                if not device or device["state"] != "device":
                    response["ok"] = False
                    state = device["state"] if device else "未出现在设备列表"
                    response["error"] = (
                        f"已重启 ADB server 并执行 adb connect {address}，"
                        f"但等待 {ADB_CONNECT_WAIT_TIMEOUT:g} 秒后设备状态仍为 {state}。"
                        "请确认手机 Wi-Fi 未休眠、无线调试端口正确，并且手机已授权。"
                    )
                    if devices_result.stderr.strip():
                        response["stderr"] = "\n".join(
                            filter(None, [response.get("stderr", ""), devices_result.stderr.strip()])
                        )
            elif initial_devices_result and initial_devices_result.stderr.strip():
                response["stderr"] = "\n".join(
                    filter(None, [response.get("stderr", ""), initial_devices_result.stderr.strip()])
                )
        json_response(self, HTTPStatus.OK if response["ok"] else HTTPStatus.BAD_REQUEST, response)

    def handle_disconnect(self, payload: dict[str, Any]) -> None:
        address = str(payload.get("address", "")).strip()
        args = [require_adb(), "disconnect"]
        if address:
            args.append(normalize_address(address))
        result = run(args, timeout=12)
        json_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, result.to_json())

    def handle_tcpip(self, payload: dict[str, Any]) -> None:
        serial = str(payload.get("serial", "")).strip()
        port = str(payload.get("port", "5555")).strip() or "5555"
        if not serial:
            raise RuntimeError("请先选择一台 USB 设备")
        result = run(adb_prefix(serial) + ["tcpip", port], timeout=12)
        if not result.ok:
            json_response(self, HTTPStatus.BAD_REQUEST, result.to_json())
            return
        time.sleep(1.5)
        ip = infer_device_ip(serial)
        response = result.to_json()
        if ip:
            address = f"{ip}:{port}"
            connect = run([require_adb(), "connect", address], timeout=12)
            response["stdout"] = "\n".join(filter(None, [response["stdout"], connect.stdout.strip(), connect.stderr.strip()]))
            response["address"] = address
            response["ok"] = connect.ok
            response["command"] = f"{response['command']} && {connect.command}"
        json_response(self, HTTPStatus.OK if response["ok"] else HTTPStatus.BAD_REQUEST, response)

    def handle_start(self, payload: dict[str, Any]) -> None:
        scrcpy = require_scrcpy()
        serial = str(payload.get("serial", "")).strip()
        args = [scrcpy]
        if serial:
            args += ["--serial", serial]

        no_audio = bool(payload.get("noAudio", payload.get("no_audio", True)))
        if no_audio:
            args.append("--no-audio")
        else:
            add_flag_arg(args, payload, "--no-audio-playback", "noAudioPlayback")
            add_flag_arg(args, payload, "--audio-dup", "audioDup")
            add_value_arg(args, payload, "--audio-codec", "audioCodec")
            add_value_arg(args, payload, "--audio-source", "audioSource")
            add_value_arg(args, payload, "--audio-bit-rate", "audioBitRate")
            add_value_arg(args, payload, "--audio-buffer", "audioBuffer")
            add_value_arg(args, payload, "--audio-output-buffer", "audioOutputBuffer")
            add_flag_arg(args, payload, "--require-audio", "requireAudio")

        add_value_arg(args, payload, "--max-size", "maxSize", "max_size")
        add_value_arg(args, payload, "--video-bit-rate", "bitRate", "bit_rate")
        add_value_arg(args, payload, "--max-fps", "maxFps")
        add_value_arg(args, payload, "--video-codec", "videoCodec")
        add_value_arg(args, payload, "--video-source", "videoSource")
        add_value_arg(args, payload, "--video-buffer", "videoBuffer")
        add_value_arg(args, payload, "--crop", "crop")
        add_value_arg(args, payload, "--display-id", "displayId")
        add_value_arg(args, payload, "--capture-orientation", "captureOrientation")
        add_flag_arg(args, payload, "--no-video", "noVideo")
        add_flag_arg(args, payload, "--no-video-playback", "noVideoPlayback")
        add_flag_arg(args, payload, "--no-downsize-on-error", "noDownsizeOnError")
        add_flag_arg(args, payload, "--no-mipmaps", "noMipmaps")
        add_flag_arg(args, payload, "--print-fps", "printFps")

        add_flag_arg(args, payload, "--fullscreen", "fullscreen")
        add_flag_arg(args, payload, "--always-on-top", "alwaysOnTop", "always_on_top")
        add_flag_arg(args, payload, "--window-borderless", "windowBorderless")
        add_value_arg(args, payload, "--window-title", "windowTitle")
        add_value_arg(args, payload, "--render-fit", "renderFit")
        add_value_arg(args, payload, "--background-color", "backgroundColor")
        add_value_arg(args, payload, "--window-width", "windowWidth")
        add_value_arg(args, payload, "--window-height", "windowHeight")
        add_value_arg(args, payload, "--window-x", "windowX")
        add_value_arg(args, payload, "--window-y", "windowY")
        add_flag_arg(args, payload, "--no-window-aspect-ratio-lock", "noWindowAspectLock")
        add_flag_arg(args, payload, "--no-window", "noWindow")

        add_flag_arg(args, payload, "--turn-screen-off", "turnScreenOff", "turn_screen_off")
        add_flag_arg(args, payload, "--stay-awake", "stayAwake", "stay_awake")
        add_flag_arg(args, payload, "--keep-active", "keepActive")
        add_flag_arg(args, payload, "--power-off-on-close", "powerOffOnClose")
        add_flag_arg(args, payload, "--no-power-on", "noPowerOn")
        add_flag_arg(args, payload, "--disable-screensaver", "disableScreensaver")
        add_flag_arg(args, payload, "--show-touches", "showTouches")
        add_value_arg(args, payload, "--screen-off-timeout", "screenOffTimeout")
        add_value_arg(args, payload, "--start-app", "startApp")

        add_value_arg(args, payload, "--keyboard", "keyboardMode")
        add_value_arg(args, payload, "--mouse", "mouseMode")
        add_value_arg(args, payload, "--gamepad", "gamepadMode")
        add_flag_arg(args, payload, "--no-control", "noControl")
        add_flag_arg(args, payload, "--prefer-text", "preferText")
        add_flag_arg(args, payload, "--no-clipboard-autosync", "noClipboardAutosync")
        add_flag_arg(args, payload, "--no-key-repeat", "noKeyRepeat")
        add_flag_arg(args, payload, "--raw-key-events", "rawKeyEvents")
        add_flag_arg(args, payload, "--no-mouse-hover", "noMouseHover")
        add_flag_arg(args, payload, "--legacy-paste", "legacyPaste")

        add_value_arg(args, payload, "--record", "recordFile")
        add_value_arg(args, payload, "--record-format", "recordFormat")
        add_value_arg(args, payload, "--record-orientation", "recordOrientation")

        add_value_arg(args, payload, "--camera-id", "cameraId")
        add_value_arg(args, payload, "--camera-facing", "cameraFacing")
        add_value_arg(args, payload, "--camera-size", "cameraSize")
        add_value_arg(args, payload, "--camera-fps", "cameraFps")
        add_value_arg(args, payload, "--camera-zoom", "cameraZoom")
        add_value_arg(args, payload, "--camera-ar", "cameraAr")
        add_flag_arg(args, payload, "--camera-torch", "cameraTorch")
        add_flag_arg(args, payload, "--camera-high-speed", "cameraHighSpeed")
        add_value_arg(args, payload, "--new-display", "newDisplay")
        add_flag_arg(args, payload, "--flex-display", "flexDisplay")
        add_flag_arg(args, payload, "--no-vd-destroy-content", "noVdDestroyContent")

        add_value_arg(args, payload, "--time-limit", "timeLimit")
        add_value_arg(args, payload, "--pause-on-exit", "pauseOnExit")
        add_value_arg(args, payload, "--verbosity", "verbosity")
        add_value_arg(args, payload, "--push-target", "pushTarget")
        add_value_arg(args, payload, "--render-driver", "renderDriver")
        add_flag_arg(args, payload, "--kill-adb-on-close", "killAdbOnClose")
        add_flag_arg(args, payload, "--no-cleanup", "noCleanup")

        extra_args = payload_text(payload, "extraArgs", "extra_args")
        if extra_args:
            args += shlex.split(extra_args)

        reconnect: list[dict[str, Any]] = []
        if serial and is_wireless_serial(serial):
            reconnect, _, _ = reconnect_wireless([serial])
            failed = next((item for item in reconnect if not item["ok"]), None)
            if failed:
                json_response(
                    self,
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": failed["message"], "reconnect": reconnect},
                )
                return

        if STATE.is_scrcpy_running():
            STATE.stop_scrcpy()

        DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_path = DEFAULT_LOG_DIR / f"scrcpy-{stamp}-{safe_name(serial)}.log"
        log_file = log_path.open("w", encoding="utf-8")
        log_file.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} scrcpy launch\n")
        log_file.write(f"$ {command_text(args)}\n\n")
        log_file.flush()

        popen_kwargs: dict[str, Any] = {
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
            "text": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(args, **popen_kwargs)
        finally:
            log_file.close()

        STATE.set_scrcpy(process, args, log_path)
        time.sleep(0.35)
        returncode = process.poll()
        if returncode is not None:
            scrcpy_status = STATE.scrcpy_status()
            json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error": f"scrcpy 启动后立即退出，退出码 {returncode}",
                    "command": command_text(args),
                    "pid": process.pid,
                    "exit_code": returncode,
                    "log_path": str(log_path),
                    "log_tail": scrcpy_status.get("log_tail", ""),
                },
            )
            return

        json_response(
            self,
            HTTPStatus.OK,
            {
                "ok": True,
                "command": command_text(args),
                "pid": process.pid,
                "log_path": str(log_path),
                "reconnect": reconnect,
            },
        )

    def handle_stop(self, payload: dict[str, Any]) -> None:
        message = STATE.stop_scrcpy()
        json_response(self, HTTPStatus.OK, {"ok": True, "message": message})

    def handle_shutdown(self, payload: dict[str, Any]) -> None:
        message = STATE.stop_scrcpy()
        json_response(self, HTTPStatus.OK, {"ok": True, "message": f"{message}；控制台服务正在关闭"})
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def handle_restart_adb(self, payload: dict[str, Any]) -> None:
        adb = require_adb()
        address = payload_text(payload, "address")
        reconnect: list[dict[str, Any]] = []
        devices: list[dict[str, str]] = []
        with ADB_COMMAND_LOCK:
            # kill-server 会断开全部无线连接，先记下来，重启后逐个连回。
            before, _ = get_adb_devices()
            addresses = [device["serial"] for device in before if is_wireless_serial(device["serial"])]
            if address:
                addresses.append(normalize_address(address))
            stop = run([adb, "kill-server"], timeout=8)
            start = run([adb, "start-server"], timeout=8)
            if start.ok:
                reconnect, devices, _ = reconnect_wireless(addresses)
        stdout = "\n".join(filter(None, [stop.stdout.strip(), start.stdout.strip()]))
        stderr = "\n".join(filter(None, [stop.stderr.strip(), start.stderr.strip()]))
        ok = start.ok
        json_response(
            self,
            HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
            {
                "ok": ok,
                "command": f"{command_text([adb, 'kill-server'])} && {command_text([adb, 'start-server'])}",
                "stdout": stdout,
                "stderr": stderr,
                "returncode": start.returncode,
                "devices": devices,
                "reconnect": reconnect,
            },
        )

    def handle_keyevent(self, payload: dict[str, Any]) -> None:
        serial = str(payload.get("serial", "")).strip()
        key = str(payload.get("key", "")).strip()
        code = KEYEVENTS.get(key)
        if code is None:
            raise RuntimeError(f"未知按键事件：{key}")
        result = run(adb_prefix(serial) + ["shell", "input", "keyevent", code], timeout=8)
        json_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, result.to_json())

    def handle_statusbar(self, payload: dict[str, Any]) -> None:
        serial = str(payload.get("serial", "")).strip()
        action = str(payload.get("action", "")).strip()
        commands = {
            "expand-notifications": ["cmd", "statusbar", "expand-notifications"],
            "expand-settings": ["cmd", "statusbar", "expand-settings"],
            "collapse": ["cmd", "statusbar", "collapse"],
        }
        command = commands.get(action)
        if command is None:
            raise RuntimeError(f"未知状态栏动作：{action}")
        result = run(adb_prefix(serial) + ["shell"] + command, timeout=8)
        json_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, result.to_json())

    def handle_screenshot(self, payload: dict[str, Any]) -> None:
        serial = str(payload.get("serial", "")).strip()
        DEFAULT_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe_serial = re.sub(r"[^A-Za-z0-9_.-]+", "_", serial or "device")
        path = DEFAULT_SCREENSHOT_DIR / f"{safe_serial}-{stamp}.png"
        args = adb_prefix(serial) + ["exec-out", "screencap", "-p"]
        try:
            completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12, check=False)
        except subprocess.TimeoutExpired:
            raise RuntimeError("截图超时")
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.decode("utf-8", errors="replace").strip() or "截图失败")
        path.write_bytes(completed.stdout)
        json_response(self, HTTPStatus.OK, {"ok": True, "path": str(path), "command": command_text(args)})

    def handle_type_text(self, payload: dict[str, Any]) -> None:
        serial = str(payload.get("serial", "")).strip()
        text = str(payload.get("text", ""))
        encoded = encode_adb_input_text(text)
        result = run(adb_prefix(serial) + ["shell", "input", "text", encoded], timeout=8)
        json_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, result.to_json())

    def handle_shell(self, payload: dict[str, Any]) -> None:
        serial = str(payload.get("serial", "")).strip()
        command = safe_shell_command(str(payload.get("command", "")))
        result = run(adb_prefix(serial) + ["shell"] + command, timeout=20)
        json_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, result.to_json())

    def handle_ls(self, payload: dict[str, Any]) -> None:
        serial = str(payload.get("serial", "")).strip()
        path = normalize_device_path(str(payload.get("path", "")))
        listing = list_device_dir(serial, path)
        listing["ok"] = True
        json_response(self, HTTPStatus.OK, listing)

    def handle_pull(self, payload: dict[str, Any]) -> None:
        serial = str(payload.get("serial", "")).strip()
        remote = normalize_device_path(str(payload.get("path", "")), default=None)
        if remote == "/":
            raise RuntimeError("不能直接下载整个根目录，请选择具体文件或目录")
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        local = unique_local_path(DOWNLOAD_DIR, posixpath.basename(remote))
        args = adb_prefix(serial) + ["pull", remote, str(local)]
        result = run(args, timeout=FILE_TRANSFER_TIMEOUT)
        response = result.to_json()
        response["remote"] = remote
        response["path"] = str(local) if result.ok else ""
        response["size"] = local.stat().st_size if result.ok and local.is_file() else None
        response["summary"] = (result.stdout or result.stderr).strip().replace(f"{remote}: ", "")
        json_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, response)

    def handle_upload(self) -> None:
        query = parse_qs(urlparse(self.path).query)

        def first(key: str) -> str:
            return (query.get(key) or [""])[0].strip()

        serial = first("serial")
        filename = safe_upload_name(first("name"))
        install = first("install") in ("1", "true", "yes")
        length = int(self.headers.get("content-length", "0") or 0)
        if length <= 0:
            raise RuntimeError("上传内容为空")
        if length > MAX_UPLOAD_BYTES:
            raise RuntimeError("文件过大，超过上传上限")
        action = "install" if install and filename.lower().endswith(".apk") else "push"
        remote = ""
        if action == "push":
            remote = posixpath.join(normalize_device_path(first("target")), filename)

        temp_dir = Path(tempfile.mkdtemp(prefix="scrcpy-control-panel-"))
        try:
            local = temp_dir / filename
            received = 0
            with local.open("wb") as stream:
                while received < length:
                    chunk = self.rfile.read(min(UPLOAD_CHUNK_BYTES, length - received))
                    if not chunk:
                        break
                    stream.write(chunk)
                    received += len(chunk)
            if received != length:
                raise RuntimeError(f"上传中断：只收到 {received} / {length} 字节")
            if action == "install":
                args = adb_prefix(serial) + ["install", "-r", str(local)]
            else:
                args = adb_prefix(serial) + ["push", str(local), remote]
            result = run(args, timeout=FILE_TRANSFER_TIMEOUT)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        response = result.to_json()
        summary = (result.stdout or result.stderr).strip().replace(f"{local}: ", "")
        response.update({"action": action, "name": filename, "remote": remote, "size": received, "summary": summary})
        json_response(self, HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST, response)

    def handle_open_downloads(self, payload: dict[str, Any]) -> None:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        requested = str(payload.get("path", "")).strip()
        target = DOWNLOAD_DIR
        reveal = False
        if requested:
            candidate = Path(requested)
            try:
                candidate.resolve().relative_to(DOWNLOAD_DIR.resolve())
            except ValueError:
                candidate = None
            if candidate is not None and candidate.exists():
                target, reveal = candidate, True
        args = open_in_file_manager(target, reveal)
        json_response(self, HTTPStatus.OK, {"ok": True, "path": str(target), "command": command_text(args)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 scrcpy 和 adb 的本地网页控制台。")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"绑定地址，默认 {DEFAULT_HOST}")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help=f"绑定端口，默认 {DEFAULT_PORT}")
    parser.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    parser.add_argument(
        "--download-dir",
        default=str(DEFAULT_DOWNLOAD_DIR),
        help=f"从手机下载文件的保存目录，默认 {DEFAULT_DOWNLOAD_DIR}",
    )
    return parser.parse_args()


def panel_is_running(url: str) -> bool:
    try:
        with urlopen(f"{url}api/status", timeout=1.5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return False
    return bool(data.get("ok"))


def main() -> int:
    global DOWNLOAD_DIR
    args = parse_args()
    DOWNLOAD_DIR = Path(args.download_dir).expanduser()
    url = f"http://{args.host}:{args.port}/"
    try:
        server = ControlPanelServer((args.host, args.port), Handler)
    except OSError:
        if panel_is_running(url):
            print(f"scrcpy 控制台已在运行：{url}", flush=True)
            if not args.no_open:
                webbrowser.open(url)
            return 0
        raise
    print(f"scrcpy 控制台 v{CONTROL_PANEL_VERSION}：{url}", flush=True)
    print("按 Ctrl+C 停止。", flush=True)
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        STATE.stop_scrcpy()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import time
import os
import random
import requests

# 智能环境配置
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":1"

if "XAUTHORITY" not in os.environ:
    if os.path.exists("/home/headless/.Xauthority"):
        os.environ["XAUTHORITY"] = "/home/headless/.Xauthority"

from seleniumbase import SB
from selenium.webdriver.common.action_chains import ActionChains

# ================= 配置区域 =================
PROXY_URL = os.getenv("PROXY", "")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
SERVERS = os.getenv("SERVERS", "").strip()

SERVER_LIST = []
if SERVERS:
    for item in SERVERS.split("|"):
        try:
            num, region = item.split(",", 1)
            SERVER_LIST.append({"num": num.strip(), "region": region.strip()})
        except:
            print(f"⚠️ SERVERS 配置格式错误: {item}")
# ===========================================

class Game4FreeRenewal:
    def __init__(self):
        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.screenshot_dir = os.path.join(self.BASE_DIR, "artifacts")
        if not os.path.exists(self.screenshot_dir):
            os.makedirs(self.screenshot_dir)

    def log(self, msg):
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [INFO] {msg}", flush=True)

    def human_wait(self, min_s=6, max_s=10):
        time.sleep(random.uniform(min_s, max_s))

    def time_to_seconds(self, t_str):
        if not t_str or "EXPIRED" in t_str.upper() or "未知" in t_str:
            return 0
        try:
            h, m, s = map(int, t_str.strip().split(':'))
            return h * 3600 + m * 60 + s
        except:
            return 0

    def send_telegram_notify(self, message, photo_path=None):
        if not TG_TOKEN or not TG_CHAT_ID:
            self.log("⚠️ 未配置 TG_TOKEN，跳过推送。")
            return
        try:
            if photo_path and os.path.exists(photo_path):
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
                with open(photo_path, 'rb') as f:
                    requests.post(url, data={'chat_id': TG_CHAT_ID, 'caption': message}, files={'photo': f})
            else:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
                requests.post(url, data={'chat_id': TG_CHAT_ID, 'text': message})
            self.log("✅ TG 推送已发送")
        except Exception as e:
            self.log(f"❌ TG 推送失败: {e}")

    def wait_for_turnstile_widget(self, sb, max_wait=30):
        """动态等待 Turnstile widget 容器加载内容"""
        self.log(f"⏳ 等待 Turnstile widget 加载 (最多 {max_wait}s)...")
        for i in range(max_wait):
            try:
                # 检查 #ts-widget 容器是否有内容（iframe 或 input）
                has_content = sb.execute_script("""
                    var w = document.querySelector('#ts-widget');
                    if (!w) return false;
                    var iframes = w.querySelectorAll('iframe');
                    if (iframes.length > 0) return true;
                    var inputs = w.querySelectorAll('input[name="cf-turnstile-response"]');
                    if (inputs.length > 0 && inputs[0].value) return true;
                    return false;
                """)
                if has_content:
                    self.log(f"✅ Turnstile widget 已加载 ({i+1}s)")
                    return True
            except:
                pass
            time.sleep(1)

        # 超时后尝试通过 JS 手动初始化 Turnstile
        self.log("⚠️ Turnstile widget 未自动加载，尝试 JS 手动初始化...")
        try:
            sb.execute_script("""
                if (typeof turnstile !== 'undefined') {
                    var w = document.querySelector('#ts-widget');
                    if (w && w.children.length === 0) {
                        turnstile.render('#ts-widget', {
                            sitekey: w.getAttribute('data-sitekey') || '',
                            callback: function(token) {
                                var input = document.createElement('input');
                                input.type = 'hidden';
                                input.name = 'cf-turnstile-response';
                                input.value = token;
                                w.appendChild(input);
                            }
                        });
                    }
                }
            """)
            time.sleep(5)
        except Exception as e:
            self.log(f"   -> JS 初始化失败: {e}")

        return False

    def find_turnstile_iframe(self, sb):
        """查找 Cloudflare Turnstile iframe，支持多种匹配方式"""
        try:
            iframes = sb.driver.find_elements("tag name", "iframe")
            self.log(f"   -> 页面共 {len(iframes)} 个 iframe")
            for f in iframes:
                src = f.get_attribute("src") or ""
                title = f.get_attribute("title") or ""
                name = f.get_attribute("name") or ""
                src_lower = src.lower()
                title_lower = title.lower()
                name_lower = name.lower()
                # 列出每个 iframe 的信息用于调试
                if src:
                    self.log(f"   -> iframe src={src[:80]}... title={title} name={name}")
                if ("cloudflare" in src_lower or "turnstile" in src_lower or
                    "challenges.cloudflare.com" in src_lower or
                    "turnstile" in title_lower or "turnstile" in name_lower or
                    "cf-turnstile" in name_lower):
                    return f
        except Exception as e:
            self.log(f"   -> ⚠️ 寻找 iframe 异常: {e}")
        return None

    def attempt_turnstile_bypass(self, sb, attempt_num):
        """尝试绕过 Cloudflare Turnstile 验证"""
        self.log(f"⚡ 尝试绕过 Cloudflare (尝试 {attempt_num}/5)...")

        # 策略 1: SeleniumBase 内置 uc_gui_handle_captcha（专治 Turnstile）
        self.log("   -> 策略 1: uc_gui_handle_captcha()...")
        try:
            sb.uc_gui_handle_captcha()
            time.sleep(3)
            token = self.get_turnstile_token(sb)
            if token:
                self.log("   -> ✅ 策略 1 成功!")
                return token
        except Exception as e:
            self.log(f"   -> 策略 1 失败: {e}")

        # 策略 2: 查找 iframe 并坐标点击
        self.log("   -> 策略 2: 查找 iframe 并坐标点击...")
        cf_iframe = self.find_turnstile_iframe(sb)
        if cf_iframe:
            size = cf_iframe.size
            width = size['width']
            self.log(f"   -> 🎯 锁定 iframe! 尺寸: {width}x{size['height']}")
            if width > 0:
                center_x_offset = int(-(width / 2) + 30)
                try:
                    for offset in [center_x_offset - 15, center_x_offset, center_x_offset + 15]:
                        ac = ActionChains(sb.driver)
                        ac.move_to_element(cf_iframe).move_by_offset(offset, 0).click().perform()
                        time.sleep(0.5)
                    time.sleep(3)
                    token = self.get_turnstile_token(sb)
                    if token:
                        self.log("   -> ✅ 策略 2 成功!")
                        return token
                except Exception as e:
                    self.log(f"   -> 🖱️ 坐标点击异常: {e}")
            else:
                self.log("   -> ⚠️ iframe 宽度为 0，可能被隐藏。")
        else:
            self.log("   -> ⚠️ 未找到 Turnstile iframe")

        # 策略 3: uc_gui_click_captcha 兜底
        self.log("   -> 策略 3: uc_gui_click_captcha() 兜底...")
        try:
            sb.uc_gui_click_captcha()
            time.sleep(3)
            token = self.get_turnstile_token(sb)
            if token:
                self.log("   -> ✅ 策略 3 成功!")
                return token
        except Exception as e:
            self.log(f"   -> 策略 3 失败: {e}")

        # 策略 4: 直接点击 #ts-widget 区域
        self.log("   -> 策略 4: 直接点击 #ts-widget 区域...")
        try:
            widget = sb.find_element("#ts-widget")
            if widget:
                ac = ActionChains(sb.driver)
                ac.move_to_element_with_offset(widget, 30, 20).click().perform()
                time.sleep(3)
                token = self.get_turnstile_token(sb)
                if token:
                    self.log("   -> ✅ 策略 4 成功!")
                    return token
        except Exception as e:
            self.log(f"   -> 策略 4 失败: {e}")

        return ""

    def get_turnstile_token(self, sb):
        """从页面提取 Turnstile token"""
        try:
            token = sb.execute_script("""
                // 尝试多种选择器
                var selectors = [
                    'input[name="cf-turnstile-response"]',
                    '#ts-widget input[name="cf-turnstile-response"]',
                    '[name="cf-turnstile-response"]'
                ];
                for (var i = 0; i < selectors.length; i++) {
                    var el = document.querySelector(selectors[i]);
                    if (el && el.value) return el.value;
                }
                return '';
            """)
            return token or ""
        except:
            return ""

    def run_single_server(self, server_num, region):
        URL_APP_PANEL = f"https://gaming4free.net/servers/{server_num}"

        self.log("=" * 40)
        self.log(f"🚀 开始续期 [{region}] ({server_num})")

        CHROMIUM_ARGS = "--no-sandbox,--disable-dev-shm-usage,--disable-gpu,--window-position=0,0,--window-size=1280,720,--disable-blink-features=AutomationControlled,--disable-infobars,--disable-popup-blocking,--disable-features=OptimizationGuideModelDownloading,OptimizationHintsFetching,OptimizationTargetPrediction"

        with SB(
            uc=True,
            test=True,
            headed=True,
            headless=False,
            xvfb=False,
            chromium_arg=CHROMIUM_ARGS,
            proxy=PROXY_URL if PROXY_URL else None
        ) as sb:
            try:
                self.log("✅ 浏览器已启动！")

                try:
                    proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
                    ip_val = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=10).json().get('ip', 'Unknown')
                    self.log(f"✅ 当前出口 IP: {ip_val}")
                except Exception:
                    self.log("⚠️ 无法获取出口 IP，跳过。")

                self.log(f"📂 正在进入续期面板 [{region}] ...")
                sb.uc_open_with_reconnect(URL_APP_PANEL, reconnect_time=7)
                self.human_wait(8, 12)

                if "login" in sb.get_current_url().lower():
                    raise Exception("登录状态失效或权限被拒绝。")

                # 点击同意 Cookies
                cookie_btns = ['//button[contains(., "Continue with Recommended Cookies")]', '//button[contains(., "Accept")]', '//button[contains(., "I Agree")]', '//button[contains(., "Consent")]']
                for btn in cookie_btns:
                    if sb.is_element_present(btn):
                        try:
                            sb.click(btn)
                            break
                        except:
                            pass

                # 获取时间
                timestamp_before = "未知"
                try:
                    sb.wait_for_element_visible('#sd-timer', timeout=15)
                    timestamp_before = sb.get_text('#sd-timer').strip()
                except:
                    pass
                self.log(f"🕒 续期前剩余运行时间: {timestamp_before}")

                ActionChains(sb.driver).scroll_by_amount(0, 600).perform()
                self.human_wait(2, 4)

                try:
                    self.log("🖱️ 正在点击 'VOTE + ADD 90 MIN'...")
                    sb.wait_for_element_visible("#sd-vote-btn", timeout=10)
                    sb.click('#sd-vote-btn')
                except Exception as e:
                    raise Exception(f"未找到打开模态框的按钮: {e}")

                # 动态等待 Turnstile widget 加载
                self.wait_for_turnstile_widget(sb, max_wait=30)

                # 尝试绕过 Turnstile（5次尝试）
                token = ""
                for attempt in range(1, 6):
                    token = self.attempt_turnstile_bypass(sb, attempt)
                    if token:
                        self.log(f"✅ 成功！已获取到 Cloudflare 凭证。")
                        break
                    self.log(f"   -> ⏳ 等待验证回调 (7 秒)...")
                    time.sleep(7)

                if not token:
                    self.log("⚠️ 未确认凭证！将尝试直接提交...")

                self.human_wait(2, 4)

                # 等待提交按钮可用
                try:
                    self.log("🖱️ 正在点击最终提交按钮 'VOTE — ADDS 90 MINUTES'...")
                    sb.wait_for_element_visible("#vm-submit", timeout=15)
                    # 等待 disabled 属性消失
                    for _ in range(10):
                        disabled = sb.execute_script(
                            "var btn = document.querySelector('#vm-submit'); return btn ? btn.disabled : true;"
                        )
                        if not disabled:
                            break
                        time.sleep(1)
                    sb.uc_click('#vm-submit')
                    self.human_wait(8, 12)
                except Exception as e:
                    raise Exception("未能点击最终的确认提交按钮")

                time.sleep(10)

                timestamp_after = "未知"
                try:
                    timestamp_after = sb.get_text('#sd-timer').strip()
                except:
                    pass
                self.log(f"🕒 续期后剩余运行时间: {timestamp_after}")

                sec_before = self.time_to_seconds(timestamp_before)
                sec_after = self.time_to_seconds(timestamp_after)

                if sec_after <= sec_before + 60 and sec_before != 0:
                    raise Exception(f"❌ 时间未增加！(前: {timestamp_before}, 后: {timestamp_after})。")

                final_screenshot = f"{self.screenshot_dir}/final_success_{server_num}.png"
                sb.save_screenshot(final_screenshot)

                msg = f"✅ [{region}] 续期成功\n🖥️ 编号: {server_num}\n🕒 续期前时间: {timestamp_before}\n🎉 续期后时间: {timestamp_after}"
                self.send_telegram_notify(msg, final_screenshot)

            except Exception as e:
                self.log(f"❌ 运行异常: {e}")
                error_shot = f"{self.screenshot_dir}/error_{server_num}.png"
                try: sb.save_screenshot(error_shot)
                except: pass
                self.send_telegram_notify(f"❌ [{region}] 执行失败: {e}\n🖥️ 编号: {server_num}", error_shot)

    def run(self):
        if not SERVER_LIST:
            self.log("❌ 未配置 SERVERS")
            return
        for server in SERVER_LIST:
            self.run_single_server(server["num"], server["region"])

if __name__ == "__main__":
    Game4FreeRenewal().run()

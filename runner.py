import sys
import argparse
from colorama import init, Fore, Style
from scenario5.config import settings
from scenario5.browser.driver import BrowserDriver
from scenario5.decision.schemas import ActionChoice, ActionType
from scenario5.agent import DualTrackAgent

init(autoreset=True)


def print_banner():
    print(f"""{Fore.CYAN}
╔══════════════════════════════════════════════════════════════════════╗
║               Scenario 5: Dual-track Browser Agent                   ║
║         (快慢双轨协同 · CDP 免驱动接管 · 真实物理终态闭环)             ║
╚══════════════════════════════════════════════════════════════════════╝{Style.RESET_ALL}""")
    print(f"{Fore.YELLOW}▶ 浏览器路径:{Style.RESET_ALL} {settings.BROWSER_PATH}")
    print(f"{Fore.YELLOW}▶ 调试端口:{Style.RESET_ALL} {settings.REMOTE_DEBUG_PORT}  |  {Fore.YELLOW}有头可见模式:{Style.RESET_ALL} {'开启' if not settings.HEADLESS else '后台静默'}")
    print(f"{Fore.YELLOW}▶ 快轨路由:{Style.RESET_ALL} {settings.FAST_PATH_MODE}  |  {Fore.YELLOW}慢轨模型:{Style.RESET_ALL} {settings.SLOW_PATH_MODEL}")
    print(f"{Fore.BLACK}{Fore.WHITE}------------------------------------------------------------------------{Style.RESET_ALL}")


def step_logger(step: int, choice: ActionChoice, desc: str):
    track_tag = f"{Fore.GREEN}[快轨 {choice.elapsed_ms:.1f}ms]{Style.RESET_ALL}" if choice.is_fast_path else f"{Fore.MAGENTA}[慢轨 LLM]{Style.RESET_ALL}"
    act_color = Fore.CYAN if choice.action == ActionType.CLICK else Fore.YELLOW if choice.action == ActionType.TYPE_TEXT else Fore.GREEN
    print(f"  {Fore.BLUE}步骤 {step:02d}{Style.RESET_ALL} {track_tag} {act_color}{choice.action.value:<9}{Style.RESET_ALL} ➜ {desc}")
    if choice.reason and choice.action != ActionType.DONE:
        print(f"          {Fore.BLACK}{Style.BRIGHT}↳ 决策依据: {choice.reason}{Style.RESET_ALL}")


def interactive_loop():
    print_banner()
    print(f"\n{Fore.GREEN}正在连接/启动 Chrome 浏览器...{Style.RESET_ALL}")
    driver = BrowserDriver()
    agent = DualTrackAgent(driver=driver, on_step_callback=step_logger)
    print(f"{Fore.GREEN}浏览器已就绪！当前页面: {driver.get_title()} ({driver.get_url()}){Style.RESET_ALL}\n")

    try:
        while True:
            print(f"{Fore.CYAN}────────────────────────────────────────────────────────────────────────{Style.RESET_ALL}")
            default_url = driver.get_url() or "https://www.baidu.com"
            url_input = input(f"{Fore.YELLOW}请输入目标网址{Style.RESET_ALL} [直接回车使用当前/默认: {default_url}]: ").strip()
            target_url = url_input if url_input else default_url

            goal = input(f"{Fore.YELLOW}请输入操作目标{Style.RESET_ALL} (例如: 搜索2026年最新AI动态 / 输入账号登录 / q 退出): ").strip()
            if not goal or goal.lower() in ("q", "quit", "exit"):
                break

            print(f"\n{Fore.CYAN}🚀 开始执行任务:{Style.RESET_ALL} {goal}")
            print(f"{Fore.CYAN}🌐 目标地址:{Style.RESET_ALL} {target_url}\n")
            
            result = agent.execute_task(goal=goal, start_url=target_url)

            print(f"\n{Fore.BLACK}{Style.BRIGHT}{'='*70}{Style.RESET_ALL}")
            if result["success"]:
                print(f"{Fore.GREEN}✔ 任务成功完成！{Style.RESET_ALL} {result['message']}")
            else:
                print(f"{Fore.RED}✘ 任务未完全通过终态校验:{Style.RESET_ALL} {result['message']}")

            print(f"  • 执行步数: {result['steps_taken']} (快轨: {result['fast_steps']}, 慢轨: {result['slow_steps']})")
            print(f"  • 快轨平均耗时: {result['avg_fast_latency_ms']} ms")
            print(f"  • 总计用时: {result['total_time_seconds']} s")
            print(f"  • 当前网址: {result['final_url']}")
            print(f"  • 页面标题: {result['final_title']}")
            print(f"{Fore.BLACK}{Style.BRIGHT}{'='*70}{Style.RESET_ALL}\n")

    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}用户中断操作。{Style.RESET_ALL}")
    finally:
        keep = input(f"{Fore.CYAN}是否保持浏览器打开以供查看？{Style.RESET_ALL} [Y/n]: ").strip().lower()
        if keep in ("n", "no"):
            print(f"{Fore.YELLOW}正在关闭浏览器...{Style.RESET_ALL}")
            driver.close()
        print(f"{Fore.GREEN}会话结束。{Style.RESET_ALL}")


def main():
    parser = argparse.ArgumentParser(description="Scenario 5 Dual-track Browser Agent")
    parser.add_argument("--url", type=str, default=None, help="目标网址")
    parser.add_argument("--goal", type=str, default=None, help="操作目标任务描述")
    parser.add_argument("--headless", action="store_true", help="使用无头静默模式")
    args = parser.parse_args()

    if args.headless:
        settings.HEADLESS = True

    if args.goal:
        # 单命令行模式
        print_banner()
        driver = BrowserDriver()
        agent = DualTrackAgent(driver=driver, on_step_callback=step_logger)
        url = args.url or "https://www.baidu.com"
        result = agent.execute_task(goal=args.goal, start_url=url)
        print(f"\n结果: {result}")
        if not args.headless:
            input("按回车键关闭浏览器...")
        driver.close()
    else:
        # 默认进入交互模式
        interactive_loop()


if __name__ == "__main__":
    main()

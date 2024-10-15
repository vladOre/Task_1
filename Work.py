#!/usr/bin/env python3

# Команда в консоль
# py Work.py --command "ping google.com" --logfile output.log --restart --timeout 60
import argparse
import sys
import shlex
import subprocess
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional
from logging.handlers import RotatingFileHandler


@dataclass
class ProcessConfig:
    """Конфигурация контролируемого процесса."""
    command: list
    logfile: str
    restart: bool = False
    timeout: Optional[int] = None


@dataclass
class RunStatistics:
    """Статистика, собранная в ходе сеанса мониторинга."""
    total_runtime: float = 0.0
    restarts: int = 0
    terminations_due_to_timeout: int = 0
    crashes: int = 0
    lines_logged: int = 0


def parse_arguments() -> ProcessConfig:
    parser = argparse.ArgumentParser(description="Скрипт мониторинга процесса")
    parser.add_argument(
        '--command',
        type=str,
        required=True,
        help='Команда для выполнения процесса, например, "ping google.com"'
    )
    parser.add_argument(
        '--logfile',
        type=str,
        required=True,
        help='Путь к файлу журнала, в который будет записан вывод'
    )
    parser.add_argument(
        '--restart',
        action='store_true',
        help='Перезапустить процесс, если он выйдет из строя'
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=None,
        help='Время в секундах, по истечении которого процесс будет завершен'
    )

    args = parser.parse_args()

    try:
        command_list = shlex.split(args.command)
    except ValueError as e:
        print(f"Ошибка команды анализа: {e}", file=sys.stderr)
        sys.exit(1)

    return ProcessConfig(
        command=command_list,
        logfile=args.logfile,
        restart=args.restart,
        timeout=args.timeout
    )


def setup_logger(logfile: str) -> logging.Logger:
    logger = logging.getLogger("ProcessMonitor")
    logger.setLevel(logging.INFO)

    # Предотвращение дублирования обработчиков при повторном использовании регистратора

    if not logger.handlers:
        # Создание обработчик файлов, чтобы предотвратить бесконечный рост журнала
        handler = RotatingFileHandler(logfile, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def monitor_output(process: subprocess.Popen, logger: logging.Logger, stats: RunStatistics):
    for line in process.stdout:
        logger.info(line.strip())
        stats.lines_logged += 1


def terminate_process(process: subprocess.Popen, logger: logging.Logger, stats: RunStatistics, due_to_timeout=False):
    if process and process.poll() is None:
        logger.info(f"Завершение процесса с помощью PID: {process.pid}.")
        try:
            process.terminate()
            logger.info("Отправлен сигнал завершения для обработки.")
            try:
                process.wait(timeout=5)
                logger.info("Процесс завершен корректно.")
            except subprocess.TimeoutExpired:
                logger.warning("Процесс не завершился вовремя; убивая это.")
                process.kill()
                process.wait()
                logger.info("Процесс убит.")
        except Exception as e:
            logger.error(f"Ошибка при завершении процесса: {e}.")
        finally:
            if due_to_timeout:
                stats.terminations_due_to_timeout += 1


def generate_report(logger: logging.Logger, stats: RunStatistics):
    total_time = stats.total_runtime
    report = (
        "\n=== Отчет о мониторинге процесса ===\n"
        f"Общее время выполнения: {total_time:.2f} сукенд\n"
        f"Количество перезапусков: {stats.restarts}\n"
        f"Прерывания из-за тайм-аута: {stats.terminations_due_to_timeout}\n"
        f"Количество сбоев: {stats.crashes}\n"
        f"Всего зарегистрированных линий: {stats.lines_logged}\n"
        "==================================\n"
    )
    logger.info(report)
class ProcessMonitor:
    """
        Отслеживает и управляет подпроцессом на основе предоставленной конфигурации.

    Attributes:
        config (ProcessConfig): Конфигурация процесса.
        logger (logging.Logger): Регистратор для регистрации выходных данных и событий процесса.
        stats (RunStatistics): Собрана статистика.
    """

    def __init__(self, config: ProcessConfig):
        self.config = config
        self.logger = setup_logger(config.logfile)
        self.stats = RunStatistics()

    def start_process(self) -> subprocess.Popen:
        """
        Запускает подпроцесс указанной командой.

        Returns:
            subprocess.Popen: Запущенный подпроцесс
        """
        self.logger.info(f"Запуск процесса: {' '.join(self.config.command)}")
        try:
            process = subprocess.Popen(
                self.config.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='cp866'
            )
            self.logger.info(f"Процесс начался с PID: {process.pid}")
            return process
        except Exception as e:
            self.logger.error(f"Не удалось запустить процесс: {e}")
            sys.exit(1)

    def handle_timeout(self, process: subprocess.Popen):
        """

        Обрабатывает завершение подпроцесса из-за тайм-аута.

        Args:
            process (subprocess.Popen): Подпроцесс для завершения.
        """
        terminate_process(process, self.logger, self.stats, due_to_timeout=True)

    def run(self):
        """
        Запускает монитор процессов, обрабатывая перезапуски и тайм-ауты в соответствии с настройками.
        """
        try:
            while True:
                process = self.start_process()
                start_time = time.time()

                output_thread = threading.Thread(target=monitor_output, args=(process, self.logger, self.stats))
                output_thread.start()

                timer = None
                if self.config.timeout:
                    self.logger.info(f"Тайм-аут установлен на {self.config.timeout} секунд.")
                    timer = threading.Timer(self.config.timeout, self.handle_timeout, args=(process,))
                    timer.start()

                process.wait()
                runtime = time.time() - start_time
                self.stats.total_runtime += runtime

                if timer:
                    timer.cancel()
                    self.logger.info("Таймер тайм-аута отменен.")

                output_thread.join()
                self.logger.info("Процесс завершен.")

                retcode = process.returncode
                if retcode != 0:
                    self.stats.crashes += 1
                    self.logger.warning(f"Процесс завершился с кодом возврата {retcode}.")

                if self.config.restart:
                    self.stats.restarts += 1
                    self.logger.info("Флаг перезапуска установлен. Перезапуск процесса.")
                    time.sleep(1)  # Brief pause before restart
                else:
                    break

        except KeyboardInterrupt:
            self.logger.info("Получено KeyboardInterrupt. Инициируем корректное завершение работы.")
            if 'process' in locals() and process.poll() is None:
                terminate_process(process, self.logger, self.stats)
        finally:
            generate_report(self.logger, self.stats)
            self.logger.info("Мониторинг процесса завершен.")


def main():
    config = parse_arguments()
    monitor = ProcessMonitor(config)
    monitor.run()
   


if __name__ == "__main__":
    main()
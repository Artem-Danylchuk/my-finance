"""Build an offline Ukrainian report from the ledger's three saved queries."""

import argparse
import calendar
import os
from datetime import date
from decimal import Decimal
from html import escape
from pathlib import Path
from string import Template
from tempfile import NamedTemporaryFile

import beanquery
from beancount import loader
from beancount.core.data import Query


ROOT = Path(__file__).resolve().parent
QUERY_NAMES = ("Витрати за категоріями", "Норма заощаджень", "Фіксовані і змінні")
MONTHS = ("", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
          "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень")
ZERO = Decimal(0)


def number(value, places=2):
    return f"{value:,.{places}f}".replace(",", "\u00a0").replace(".", ",")


def money(value):
    return f"{number(value)} zł"


def percent(value):
    return "не визначено" if value is None else f"{number(value, 1)}%"


def month_label(month):
    return f"{MONTHS[month.month]} {month.year}"


def category_label(account):
    return account.split(":", 1)[1].replace(":", " / ").replace("-", " ").capitalize()


def read_results(ledger):
    entries, errors, options = loader.load_file(str(ledger))
    if errors:
        raise ValueError(f"Облік містить помилки ({len(errors)}); запусти uv run bean-check {ledger.name}")
    queries = {}
    for entry in entries:
        if isinstance(entry, Query) and entry.name in QUERY_NAMES:
            if entry.name in queries:
                raise ValueError(f"Назва запиту повторюється: {entry.name}")
            queries[entry.name] = entry.query_string
    missing = set(QUERY_NAMES) - queries.keys()
    if missing:
        raise ValueError(f"Відсутні збережені запити: {', '.join(sorted(missing))}")
    connection = beanquery.connect("beancount:", entries=entries, errors=errors, options=options)
    # Execute the exact saved SQL, without copying its calculations into Python.
    return {name: connection.execute(queries[name]).fetchall() for name in QUERY_NAMES}


def bars(rows, label):
    maximum = max((abs(amount) for _, amount, _ in rows), default=ZERO) or Decimal(1)
    items = []
    for title, amount, color in rows:
        width = abs(amount) / maximum * 100
        items.append(f'''<li class="bar-row">
          <div class="bar-label"><span>{escape(title)}</span><strong>{money(amount)}</strong></div>
          <div class="track" aria-hidden="true"><span class="fill {color}"
            style="width:{width:.4f}%"></span></div></li>''')
    if not items:
        return '<p class="empty">У цьому місяці витрат немає.</p>'
    return f'<ul class="chart" aria-label="{escape(label, quote=True)}">' + "".join(items) + "</ul>"


def card(index, title, headline, caption, chart, conclusion, note=""):
    return f'''<article class="card card-{index}">
      <div class="eyebrow"><span class="step">0{index}</span> {escape(title)}</div>
      <div class="headline">{escape(headline)}</div>
      <p class="caption">{escape(caption)}</p>
      {chart}<p class="conclusion">{escape(conclusion)}</p>
      {f'<p class="note">{escape(note)}</p>' if note else ''}</article>'''


def render_report(results, through):
    categories, savings, fixed = (results[name] for name in QUERY_NAMES)
    if not savings:
        raise ValueError("Немає місяців із доходами або витратами")
    if max(row[5] for row in savings) > through:
        raise ValueError("Дата покриття раніша за останню операцію в обліку")
    categories_by_month = {}
    for month, account, amount in categories:
        categories_by_month.setdefault(month, []).append((account, amount))
    fixed_by_month = {month: (fixed_amount, variable) for month, fixed_amount, variable in fixed}
    options, panels = [], []
    savings = sorted(savings)
    for month, income, expenses, saved, rate, last_operation in savings:
        category_rows = sorted(categories_by_month.get(month, []), key=lambda row: (-row[1], row[0]))
        fixed_amount, variable = fixed_by_month.get(month, (ZERO, ZERO))
        if sum((amount for _, amount in category_rows), ZERO) != expenses:
            raise ValueError(f"{month}: сума категорій не збігається з витратами")
        if fixed_amount + variable != expenses or income - expenses != saved:
            raise ValueError(f"{month}: результати запитів не узгоджуються")
        month_end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
        partial = through < month_end
        status = (f"Неповний місяць · дані до {through:%d.%m.%Y}" if partial
                  else f"Повний місяць · до {month_end:%d.%m.%Y}")
        active = month == savings[-1][0]
        options.append(f'<option value="{month}"{" selected" if active else ""}>'
                       f'{month_label(month)}{" · неповний" if partial else ""}</option>')
        category_chart = bars([(category_label(account),
                                amount, "teal") for account, amount in category_rows],
                              "Витрати за категоріями у злотих")
        if category_rows:
            largest_name, largest = category_rows[0]
            largest_name = category_label(largest_name)
            category_sentence = f"Найбільша категорія — «{largest_name}»: {money(largest)}."
        else:
            category_sentence = "За цей місяць витрат не зафіксовано."
        first = card(1, "Куди йдуть гроші?", money(expenses), "Усього витрачено за місяць",
                     category_chart, category_sentence)

        if rate is None:
            savings_sentence = f"Дохід дорівнює нулю, тому норму заощаджень не визначено; різниця — {money(saved)}."
        elif saved < 0:
            savings_sentence = f"Витрати перевищили дохід на {money(-saved)}, тому заощадження від’ємні."
        else:
            savings_sentence = f"Із доходу залишилося {money(saved)} — це {percent(rate)}."
        savings_chart = bars([("Дохід", income, "teal"), ("Витрати", expenses, "orange"),
                              ("Відкладено", saved, "rose" if saved < 0 else "green")],
                             "Дохід, витрати та заощадження у злотих")
        second = card(2, "Яку частку доходу відкладаю?", "—" if rate is None else percent(rate),
                      "Норма заощаджень · дохід мінус витрати", savings_chart, savings_sentence)

        fixed_rate = fixed_amount / expenses * 100 if expenses != 0 else None
        unknown = sum((amount for account, amount in category_rows
                       if account == "Expenses:Без-Категорії"), ZERO)
        note = (f"Без категорії — {money(unknown)}; серед цих витрат можуть бути фіксовані платежі."
                if unknown else "")
        fixed_chart = bars([("Фіксовані", fixed_amount, "teal"), ("Змінні", variable, "orange")],
                           "Фіксовані та змінні витрати у злотих")
        fixed_caption = f"Фіксовані витрати · {percent(fixed_rate)} від усіх витрат" if expenses else "Фіксовані витрати · частка не визначена"
        third = card(3, "Скільки витрат можу змінювати?", money(fixed_amount), fixed_caption,
                     fixed_chart, f"За поточним розподілом змінні витрати становлять {money(variable)}.", note)
        panels.append(f'''<section class="month-panel" id="month-{month}" data-month="{month}"
          aria-label="{month_label(month)}"{'' if active else ' hidden'}>
          <div class="period"><span class="badge{' partial' if partial else ''}">{status}</span>
          <span>Остання операція: {last_operation:%d.%m.%Y}</span></div>
          <div class="cards">{first}{second}{third}</div></section>''')
    template = Template((ROOT / "report_template.html").read_text(encoding="utf-8"))
    return template.substitute(options="".join(options), panels="".join(panels),
                               through=through.strftime("%d.%m.%Y"))


def generate(ledger, output, through):
    # Finish loading, validating, querying, and rendering before replacing the previous report.
    content = render_report(read_results(ledger), through)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=output.parent,
                                prefix=f".{output.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Автономний місячний звіт Олі українською")
    parser.add_argument("--through", required=True, type=date.fromisoformat,
                        help="підтверджена дата покриття виписки, YYYY-MM-DD")
    args = parser.parse_args()
    try:
        generate(ROOT / "main.beancount", ROOT / "report.html", args.through)
    except Exception as exc:
        raise SystemExit(f"Не вдалося створити звіт: {exc}") from exc
    print(f"Створено {ROOT / 'report.html'}; дані до {args.through:%d.%m.%Y}.")


if __name__ == "__main__":
    main()

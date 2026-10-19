"""Build an offline Ukrainian report from the ledger's three saved queries."""

import argparse
import calendar
import json
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


def read_results(ledger, adjustments=""):
    if adjustments:
        entries, errors, options = loader.load_string(
            f'include {json.dumps(str(ledger.resolve()), ensure_ascii=False)}\n{adjustments}\n')
    else:
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


def render_shared(context, months):
    if context is None:
        return '<p class="empty">Спільні дані не підключено.</p>'
    from household_report import month_summary
    panels = []
    for month in months:
        end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
        if end < context["since"]:
            content = '<p class="empty">Спільний облік ще не почався. Історія Олі збережена.</p>'
        elif end < context["marko_start"] or month > context["marko_through"]:
            content = '<p class="empty">Немає виписки Марка за цей період. Загальний підсумок не визначено.</p>'
        else:
            summary = month_summary(context, month)
            names = {"olya": "Оля", "marko": "Марко"}
            settlement = (f"{names[summary.debtor]} → {names[summary.creditor]}: {money(summary.due)}"
                          if summary.debtor else "Усе порівну — компенсація не потрібна.")
            coverage = min(context["olya_through"], context["marko_through"], end)
            partial = (coverage < end or context["marko_start"] > month or context["since"] > month)
            status = f"{'Неповний місяць' if partial else 'Повний місяць'} · спільне покриття до {coverage:%d.%m.%Y}"
            categories = {}
            for expense in context["expenses"]:
                if expense.day.replace(day=1) == month:
                    categories[expense.category] = categories.get(expense.category, ZERO) + expense.amount
            chart = bars([(category_label(account), amount, "teal")
                          for account, amount in sorted(categories.items())], "Спільні витрати")
            content = f'''<div class="period"><span class="badge{' partial' if partial else ''}">{status}</span>
              <span>Оплати Олі до {min(end, context['olya_through']):%d.%m.%Y}; Марка до {min(end, context['marko_through']):%d.%m.%Y}</span></div>
              <p class="note">Дані Марка вигадані · компенсації ще не внесено. Враховано всі наявні оплати кожного за його період покриття.</p><div class="cards">'''
            content += card(1, "Спільні витрати", money(summary.total), "Оренда та продукти · 50/50", chart,
                            "Кожна покупка врахована один раз. Частки округлюються до гроша для кожної покупки.")
            content += card(2, "Хто скільки оплатив", money(summary.olya.paid), "Фактично оплатила Оля",
                            bars([("Оля", summary.olya.paid, "teal"), ("Марко", summary.marko.paid, "orange")], "Оплати"),
                            f"Частка Олі — {money(summary.olya.share)}; Марка — {money(summary.marko.share)}.")
            content += card(3, "Хто кому компенсує", money(summary.due), "За обраний місяць",
                            "", settlement, "Борг інших місяців не переноситься.") + '</div>'
        panels.append(f'<div class="shared-month-panel" data-month="{month}"'
                      f'{"" if month == months[-1] else " hidden"}>{content}</div>')
    return ''.join(panels)


def render_report(results, through, shared_context=None):
    categories, savings, fixed = (results[name] for name in QUERY_NAMES)
    if not savings:
        raise ValueError("Немає місяців із доходами або витратами")
    if max(row[5] for row in savings) > through:
        raise ValueError("Дата покриття раніша за останню операцію в обліку")
    categories_by_month = {}
    for month, account, amount in categories:
        categories_by_month.setdefault(month, []).append((account, amount))
    fixed_by_month = {month: (fixed_amount, variable) for month, fixed_amount, variable in fixed}
    options, panels, export_data = [], [], {}
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
        payment_note = (f'<p class="note">Витрати, оплачені з твого банку: '
                        f'{money(shared_context["paid_by_month"].get(month, ZERO))}. '
                        f'Твої витрати після розподілу: {money(expenses)}.</p>'
                        if shared_context else "")
        panels.append(f'''<section class="month-panel" id="month-{month}" data-month="{month}"
          aria-label="{month_label(month)}"{'' if active else ' hidden'}>
          <div class="period"><span class="badge{' partial' if partial else ''}">{status}</span>
          <span>Остання операція: {last_operation:%d.%m.%Y}</span></div>
          {payment_note}<div class="cards">{first}{second}{third}</div></section>''')
        from exports import encoded
        rows = [["Показник / категорія", "Сума PLN"],
                ["Період", month_label(month)], ["Покриття Олі", through.isoformat()],
                ["Дохід", income], ["Витрати за своєю часткою", expenses],
                ["Дохід мінус витрати", saved], ["Фіксовані", fixed_amount], ["Змінні", variable]]
        rows.extend([category_label(account), amount] for account, amount in category_rows)
        export_data[f"olya:{month}"] = encoded(rows)
        if shared_context:
            from household_report import month_summary
            if (month_end >= max(shared_context["since"], shared_context["marko_start"])
                    and month <= shared_context["marko_through"]):
                summary = month_summary(shared_context, month)
                export_data[f"shared:{month}"] = encoded([
                    ["Показник", "Сума PLN / значення"], ["Період", month_label(month)],
                    ["Дані Марка", "Вигадані"], ["Покриття Олі", min(month_end, through).isoformat()],
                    ["Покриття Марка", min(month_end, shared_context["marko_through"]).isoformat()],
                    ["Спільні витрати", summary.total], ["Оплати Олі", summary.olya.paid],
                    ["Оплати Марка", summary.marko.paid], ["Частка Олі", summary.olya.share],
                    ["Частка Марка", summary.marko.share], ["Компенсація", summary.due],
                    ["Платник компенсації", summary.debtor or "Боргу немає"],
                    ["Отримувач компенсації", summary.creditor or "Боргу немає"],
                    ["Статус", "За наявними виписками; компенсації ще не внесено"],
                ])
    template = Template((ROOT / "report_template.html").read_text(encoding="utf-8"))
    context_note = (f"Від {shared_context['since']:%d.%m.%Y} оренда та продукти враховані за твоєю часткою 50/50. "
                    f"Дані Марка вигадані, до {shared_context['marko_through']:%d.%m.%Y}; "
                    "заощадження враховують цей розподіл. Якщо виписки Марка за місяць немає, "
                    "показано твої фактичні оплати. Попередні місяці збережено."
                    if shared_context else "Наявна виписка Олі без розподілу спільних витрат.")
    from savings import target
    goal = target(results[QUERY_NAMES[1]], through)
    goal_html = (f'<p>Орієнтир: <strong>{money(goal["target"])}</strong> — шість середніх місяців витрат. '
                 f'Середнє: {money(goal["average"])}. Основа: {goal["months"]} повні місяці.</p>'
                 '<p class="note">Історичні місяці до жовтня відображають витрати Олі до розподілу 50/50. '
                 'Орієнтир зміниться з новими повними місяцями.</p>' if goal else
                 '<p>Ще немає повного місяця для розрахунку цілі. Вкажи місячні витрати нижче.</p>')
    return template.substitute(options="".join(options), panels="".join(panels),
                               through=through.strftime("%d.%m.%Y"),
                               shared_panels=render_shared(shared_context, [row[0] for row in savings]),
                               context_note=escape(context_note), goal_html=goal_html,
                               goal_target=str(goal["target"] if goal else ""),
                               export_data=json.dumps(export_data, ensure_ascii=False).replace("<", "\\u003c"))


def generate(ledger, output, through, household=None):
    # Finish loading, validating, querying, and rendering before replacing the previous report.
    context = None
    if household is not None:
        from household_report import load_context
        context = load_context(ledger, household, through)
    content = render_report(read_results(ledger, context["adjustments"] if context else ""),
                            through, context)
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
        generate(ROOT / "main.beancount", ROOT / "report.html", args.through,
                 household=ROOT / "household.json")
    except Exception as exc:
        raise SystemExit(f"Не вдалося створити звіт: {exc}") from exc
    print(f"Створено {ROOT / 'report.html'}; дані до {args.through:%d.%m.%Y}.")


if __name__ == "__main__":
    main()

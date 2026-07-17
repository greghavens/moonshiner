# Saturday-market settlement book. Every stall owes the market a cut of
# each paid sale, plus a flat table fee per table its section charges.
# The office runs this after close to know who owes what.

Sale = Struct.new(:item, :amount, :paid)
Stall = Struct.new(:name, :sales)
Section = Struct.new(:name, :table_fee, :stalls)

module StallBook
  COMMISSION = 0.06

  module_function

  # One row per paid sale: which stall, what sold, what the market is due.
  def settlement_rows(sections)
    rows = []
    sections.each do |section|
      section.stalls.each do |stall|
        stall.sales.select { |sale| sale.paid }.each do |sale|
          rows << {
            section: section.name,
            stall: stall.name,
            item: sale.item,
            due: (sale.amount * COMMISSION).round(2)
          }
        end
      end
    end
    rows
  end

  # Total owed per section: table fees for every stall present, plus
  # commission on everything that actually got paid for.
  def section_totals(sections)
    totals = Hash.new { |h, k| h[k] = 0.0 }
    sections.each do |section|
      totals[section.name] += section.table_fee * section.stalls.length
      section.stalls.each do |stall|
        stall.sales.each { |sale|
          totals[section.name] += (sale.amount * COMMISSION).round(2) if sale.paid
        }
      end
    totals
  end

  # The section the office chases first on Monday.
  def owes_most(sections)
    name, due = section_totals(sections).max_by { |_name, due| due }
    { section: name, due: due.round(2) }
  end

  # Items that walked away unpaid, grouped for the incident sheet.
  def unpaid_items(sections)
    items = []
    sections.each do |section|
      section.stalls.each do |stall|
        stall.sales.reject { |sale| sale.paid }.each do |sale|
          items << "#{section.name}/#{stall.name}: #{sale.item}"
        end
      end
    end
    items
  end
end

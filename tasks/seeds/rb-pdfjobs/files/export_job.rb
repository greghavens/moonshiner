# Background worker job for Ledgerline's statement exports. Each customer
# statement is rendered by one ExportJob; render options come from the
# factory defaults plus whatever the requesting tool overrides. Jobs are
# built in bursts by the scheduler, all inside one worker process.
class ExportJob
  DEFAULTS = {
    page_size: "Letter",
    orientation: "portrait",
    copies: 1,
    stamp_lines: [],
    delivery: { emails: [], attach_csv: false }
  }.freeze

  attr_reader :doc_id

  def initialize(doc_id, overrides = {})
    @doc_id = doc_id
    @options = DEFAULTS.dup
    overrides.each { |key, value| configure(key, value) }
  end

  # Point tweaks after the job is queued (the review UI uses this).
  def configure(key, value)
    raise ArgumentError, "unknown option #{key.inspect}" unless DEFAULTS.key?(key)

    @options[key] = value
    self
  end

  # Diagonal text stamped across every page ("DRAFT", "PAID", ...).
  def add_stamp(text)
    @options[:stamp_lines] << text.to_s.upcase
    self
  end

  # CC a copy of the finished PDF when the render completes.
  def deliver_to(email)
    @options[:delivery][:emails] << email
    self
  end

  # Bundle the raw line items as a CSV attachment alongside the PDF.
  def attach_csv!
    @options[:delivery][:attach_csv] = true
    self
  end

  def options
    @options
  end

  # One line per job in the worker log; ops greps these during audits.
  def manifest
    stamps = @options[:stamp_lines].empty? ? "-" : @options[:stamp_lines].join("+")
    ccs = @options[:delivery][:emails].empty? ? "-" : @options[:delivery][:emails].join(",")
    bundle = @options[:delivery][:attach_csv] ? "csv" : "pdf-only"
    format("%s %s/%s x%d stamps=%s cc=%s %s",
           @doc_id, @options[:page_size], @options[:orientation],
           @options[:copies], stamps, ccs, bundle)
  end
end

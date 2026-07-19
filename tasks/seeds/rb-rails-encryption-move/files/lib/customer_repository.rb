class CustomerRepository
  ValidationError = Class.new(StandardError)

  attr_reader :write_count

  def initialize(nonce_source:)
    @nonce_source = nonce_source
    @rows = {}
    @write_count = 0
  end

  def save(customer)
    unless customer.valid?
      raise ValidationError, customer.errors.inspect
    end

    ciphertext = Customer.encrypt_attribute(
      :contact_code,
      customer.contact_code,
      nonce: @nonce_source.call
    )
    @rows[customer.id] = { name: customer.name, contact_code: ciphertext }
    @write_count += 1
    ciphertext
  end

  def import_row(id:, name:, contact_code:)
    @rows[id] = { name: name, contact_code: contact_code }
  end

  def find(id)
    row = @rows.fetch(id)
    Customer.new(
      id: id,
      name: row.fetch(:name),
      contact_code: Customer.decrypt_attribute(:contact_code, row.fetch(:contact_code))
    )
  end

  def ciphertext_for(id)
    @rows.fetch(id).fetch(:contact_code).dup
  end
end

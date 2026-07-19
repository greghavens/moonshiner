class Customer
  include Framework72::EncryptedAttributes

  encrypts :contact_code, scheme: CustomerEncryption.active_scheme

  attr_reader :id, :name, :contact_code, :errors

  def initialize(id:, name:, contact_code:)
    @id = id
    @name = name
    @contact_code = contact_code
    @errors = {}
  end

  def valid?
    @errors = {}
    add_error(:name, "can't be blank") if @name.nil? || @name.strip.empty?
    if @contact_code.nil? || @contact_code.strip.empty?
      add_error(:contact_code, "can't be blank")
    elsif !@contact_code.match?(/\Acustomer-code-[a-z]+\z/)
      add_error(:contact_code, "has invalid format")
    end
    @errors.empty?
  end

  private

  def add_error(field, message)
    (@errors[field] ||= []) << message
  end
end

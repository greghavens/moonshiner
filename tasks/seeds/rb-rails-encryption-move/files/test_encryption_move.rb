require "json"
require "minitest/autorun"
require_relative "customer_system"

class NonceSource
  attr_reader :calls

  def initialize(*values)
    @values = values
    @calls = 0
  end

  def call
    value = @values.fetch(@calls)
    @calls += 1
    value
  end
end

class EncryptionMoveTest < Minitest::Test
  def self.run_order
    :alpha
  end

  def fixtures
    @fixtures ||= JSON.parse(File.read(File.join(__dir__, "fixtures/encryption_rotation.json")))
  end

  def repository(*nonces)
    source = NonceSource.new(*nonces)
    [CustomerRepository.new(nonce_source: source), source]
  end

  def test_01_model_declaration_satisfies_the_current_framework_contract
    assert_equal true, Customer.validate_encryption_contract!
  end

  def test_02_protected_rotation_ciphertexts_remain_readable_without_rewrite
    repo, source = repository("unused-nonce")
    fixtures.each do |fixture|
      repo.import_row(
        id: fixture.fetch("id"),
        name: fixture.fetch("name"),
        contact_code: fixture.fetch("ciphertext")
      )
      before = repo.ciphertext_for(fixture.fetch("id"))
      loaded = repo.find(fixture.fetch("id"))
      assert_equal fixture.fetch("plaintext"), loaded.contact_code
      assert_equal before, repo.ciphertext_for(fixture.fetch("id"))
    end
    assert_equal 0, repo.write_count
    assert_equal 0, source.calls
  end

  def test_03_new_writes_use_the_primary_scheme_and_fresh_nonces
    repo, source = repository("nonce-0001", "nonce-0002")
    first = repo.save(Customer.new(id: "C-9", name: "North", contact_code: "customer-code-new"))
    second = repo.save(Customer.new(id: "C-10", name: "South", contact_code: "customer-code-new"))

    assert first.start_with?("v2:rotate-b:nonce-0001:")
    assert second.start_with?("v2:rotate-b:nonce-0002:")
    refute_equal first, second
    refute_includes first, "customer-code-new"
    assert_equal "customer-code-new", repo.find("C-9").contact_code
    assert_equal "customer-code-new", repo.find("C-10").contact_code
    assert_equal 2, repo.write_count
    assert_equal 2, source.calls
  end

  def test_04_plaintext_validation_precedes_encryption_and_write
    repo, source = repository("nonce-must-remain")
    invalid = Customer.new(id: "C-BAD", name: "", contact_code: "not a customer code")
    error = assert_raises(CustomerRepository::ValidationError) { repo.save(invalid) }
    assert_includes error.message, ":name=>[\"can't be blank\"]"
    assert_includes error.message, ":contact_code=>[\"has invalid format\"]"
    assert_equal 0, repo.write_count
    assert_equal 0, source.calls

    blank = Customer.new(id: "C-BLANK", name: "Name", contact_code: " ")
    assert_raises(CustomerRepository::ValidationError) { repo.save(blank) }
    assert_equal({ contact_code: ["can't be blank"] }, blank.errors)
    assert_equal 0, source.calls
  end

  def test_05_tampering_and_unknown_keys_are_not_swallowed
    repo, = repository("unused")
    ciphertext = fixtures.fetch(1).fetch("ciphertext")
    tampered = ciphertext.sub(/.$/, ciphertext.end_with?("0") ? "1" : "0")
    repo.import_row(id: "C-TAMPER", name: "Tampered", contact_code: tampered)
    error = assert_raises(Framework72::DecryptionError) { repo.find("C-TAMPER") }
    assert_equal "ciphertext authentication failed", error.message

    unknown = ciphertext.sub(":rotate-a:", ":missing-key:")
    repo.import_row(id: "C-UNKNOWN", name: "Unknown", contact_code: unknown)
    error = assert_raises(Framework72::DecryptionError) { repo.find("C-UNKNOWN") }
    assert_equal "unknown encryption scheme v2:missing-key", error.message
    assert_equal 0, repo.write_count
  end

  def test_06_local_notes_record_old_new_and_rotation_contracts
    notes = File.read(File.join(__dir__, "contracts/framework_7_2_encryption.md"))
    [
      "`encrypts(name, scheme:)`",
      "`encrypted_attribute(name, key_provider:)`",
      "per-write nonce",
      "rejected record does not",
      "side-effect free"
    ].each { |phrase| assert_includes notes, phrase }
  end
end

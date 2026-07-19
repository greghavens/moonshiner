require "digest"

module Framework72
  ConfigurationError = Class.new(StandardError)
  DecryptionError = Class.new(StandardError)

  Scheme = Data.define(:version, :key_id, :material)

  class KeyProvider
    attr_reader :primary, :previous

    def initialize(primary:, previous: [])
      raise ArgumentError, "primary scheme required" unless primary.is_a?(Scheme)
      unless previous.all? { |scheme| scheme.is_a?(Scheme) }
        raise ArgumentError, "previous schemes must be Scheme values"
      end

      @primary = primary
      @previous = previous.freeze
    end

    def readable
      [@primary, *@previous]
    end
  end

  module Cipher
    module_function

    def encrypt(plaintext, scheme, nonce:)
      case scheme.version
      when "v1"
        build("v1", scheme, "-", plaintext)
      when "v2"
        raise ArgumentError, "nonce required" if nonce.nil? || nonce.empty?
        build("v2", scheme, nonce, plaintext)
      else
        raise ArgumentError, "unsupported scheme version #{scheme.version}"
      end
    end

    def decrypt(ciphertext, schemes)
      version, key_id, nonce, payload, supplied_tag = ciphertext.to_s.split(":", 5)
      unless %w[v1 v2].include?(version) && key_id && nonce && payload && supplied_tag
        raise DecryptionError, "malformed encrypted attribute"
      end
      scheme = schemes.find { |candidate| candidate.version == version && candidate.key_id == key_id }
      raise DecryptionError, "unknown encryption scheme #{version}:#{key_id}" unless scheme

      expected = tag(version, scheme, nonce, payload)
      raise DecryptionError, "ciphertext authentication failed" unless secure_equal?(supplied_tag, expected)

      bytes = [payload].pack("H*")
      xor(bytes, stream(scheme, nonce)).force_encoding(Encoding::UTF_8).tap do |value|
        raise DecryptionError, "decrypted attribute is not UTF-8" unless value.valid_encoding?
      end
    rescue ArgumentError
      raise DecryptionError, "malformed encrypted attribute"
    end

    def build(version, scheme, nonce, plaintext)
      payload = xor(plaintext.encode(Encoding::UTF_8), stream(scheme, nonce)).unpack1("H*")
      [version, scheme.key_id, nonce, payload, tag(version, scheme, nonce, payload)].join(":")
    end
    private_class_method :build

    def stream(scheme, nonce)
      Digest::SHA256.digest("#{scheme.material}|#{nonce}")
    end
    private_class_method :stream

    def xor(value, key)
      value.bytes.each_with_index.map { |byte, index| byte ^ key.getbyte(index % key.bytesize) }.pack("C*")
    end
    private_class_method :xor

    def tag(version, scheme, nonce, payload)
      Digest::SHA256.hexdigest([version, scheme.material, nonce, payload].join("|"))[0, 20]
    end
    private_class_method :tag

    def secure_equal?(left, right)
      return false unless left.bytesize == right.bytesize

      left.bytes.zip(right.bytes).reduce(0) { |difference, pair| difference | (pair[0] ^ pair[1]) }.zero?
    end
    private_class_method :secure_equal?
  end

  module EncryptedAttributes
    def self.included(base)
      base.extend(ClassMethods)
    end

    module ClassMethods
      def encrypts(name, scheme:)
        encryption_declarations[name.to_sym] = { removed: "encrypts", scheme: scheme }
      end

      def encrypted_attribute(name, key_provider:)
        encryption_declarations[name.to_sym] = { key_provider: key_provider }
      end

      def validate_encryption_contract!
        encryption_declarations.each do |name, declaration|
          next unless declaration[:removed]

          raise ConfigurationError,
                "#{self}.#{name} uses removed #{declaration[:removed]} encrypted-attribute API"
        end
        true
      end

      def encrypt_attribute(name, plaintext, nonce:)
        declaration = current_declaration(name)
        Cipher.encrypt(plaintext, declaration[:key_provider].primary, nonce: nonce)
      end

      def decrypt_attribute(name, ciphertext)
        declaration = current_declaration(name)
        Cipher.decrypt(ciphertext, declaration[:key_provider].readable)
      end

      private

      def encryption_declarations
        @encryption_declarations ||= {}
      end

      def current_declaration(name)
        validate_encryption_contract!
        declaration = encryption_declarations.fetch(name.to_sym) do
          raise ConfigurationError, "encrypted attribute #{name} is not declared"
        end
        unless declaration[:key_provider].is_a?(KeyProvider)
          raise ConfigurationError, "encrypted attribute #{name} requires a key provider"
        end
        declaration
      end
    end
  end
end

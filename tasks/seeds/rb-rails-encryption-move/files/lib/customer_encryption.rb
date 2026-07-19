module CustomerEncryption
  LEGACY_SCHEME = Framework72::Scheme.new("v1", "legacy-a", "fixture-legacy-material")
  PRIOR_SCHEME = Framework72::Scheme.new("v2", "rotate-a", "fixture-prior-material")
  CURRENT_SCHEME = Framework72::Scheme.new("v2", "rotate-b", "fixture-current-material")

  module_function

  def active_scheme
    LEGACY_SCHEME
  end
end

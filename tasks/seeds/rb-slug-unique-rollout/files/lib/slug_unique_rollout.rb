# frozen_string_literal: true

require "thread"

module SlugUniqueRollout
  class ConstraintViolation < StandardError; end
  class DuplicateSlug < ConstraintViolation; end
  class MigrationIncomplete < ConstraintViolation; end

  Post = Struct.new(:id, :title, :legacy_slug, :slug, keyword_init: true)
  BatchResult = Struct.new(
    :cursor,
    :scanned_ids,
    :updated_ids,
    :done,
    keyword_init: true
  )

  module Slugger
    module_function

    def base_for(title)
      slug = title.to_s.downcase
                  .gsub(/[^a-z0-9]+/, "-")
                  .gsub(/\A-+|-+\z/, "")
      slug.empty? ? "post" : slug
    end
  end

  class Store
    def initialize(rows)
      @mutex = Mutex.new
      @posts = {}
      @aliases = {}
      @write_counts = Hash.new(0)
      @slug_reservations = {}
      @expanded = false
      @unique_slugs_enforced = false

      rows.each { |attributes| load_post(attributes) }
      load_existing_aliases
    end

    def expand!
      @mutex.synchronize { @expanded = true }
      true
    end

    # Models an old application writer which does not know about canonical slugs.
    def insert_legacy(title:, legacy_slug:)
      @mutex.synchronize do
        if @unique_slugs_enforced
          raise ConstraintViolation, "slug is required after rollout finalization"
        end

        id = next_id_locked
        add_post_locked(id: id, title: title, legacy_slug: legacy_slug, slug: nil)
        copy_post(@posts.fetch(id))
      end
    end

    # Expanded-phase writes populate both the canonical slug and old lookup alias.
    def create(title:, legacy_slug:)
      @mutex.synchronize do
        raise ConstraintViolation, "slug rollout has not been expanded" unless @expanded

        id = next_id_locked
        base = Slugger.base_for(title)
        slug = next_available_slug_locked(base, except_id: id)
        add_post_locked(id: id, title: title, legacy_slug: legacy_slug, slug: slug)
        install_alias_locked(@posts.fetch(id))
        @write_counts[id] += 1
        copy_post(@posts.fetch(id))
      end
    end

    def scan_after(cursor:, limit:)
      unless limit.is_a?(Integer) && limit.positive?
        raise ArgumentError, "limit must be a positive integer"
      end
      unless cursor.nil? || cursor.is_a?(Integer)
        raise ArgumentError, "cursor must be an integer or nil"
      end

      @mutex.synchronize do
        floor = cursor || -1
        selected = @posts.values
                         .select { |post| post.id > floor }
                         .sort_by(&:id)
                         .first(limit)
        next_cursor = selected.empty? ? cursor : selected.last.id
        done = @posts.keys.none? { |id| id > (next_cursor || floor) }
        [selected.map { |post| copy_post(post) }, next_cursor, done]
      end
    end

    # Returns [slug, changed]. The callback is a deterministic concurrency/failure
    # seam: production callers omit it.
    def claim_slug(id, before_commit: nil)
      choice = @mutex.synchronize do
        post = @posts.fetch(id)
        if post.slug
          install_alias_locked(post)
          [:existing, post.slug]
        else
          candidate = next_available_slug_locked(
            Slugger.base_for(post.title),
            except_id: id
          )
          [:candidate, candidate]
        end
      end

      return [choice.last, false] if choice.first == :existing

      candidate = choice.last
      result = nil
      begin
        before_commit&.call(id, candidate)
        result = @mutex.synchronize do
          post = @posts.fetch(id)
          if post.slug
            install_alias_locked(post)
            [post.slug, false]
          else
            if @unique_slugs_enforced &&
               slug_unavailable_locked?(candidate, except_id: id)
              raise ConstraintViolation, "slug #{candidate.inspect} is no longer available"
            end

            post.slug = candidate
            install_alias_locked(post)
            @write_counts[id] += 1
            [candidate, true]
          end
        end
      ensure
        @mutex.synchronize do
          if @slug_reservations[candidate] == id
            @slug_reservations.delete(candidate)
          end
        end
      end
      result
    end

    def finalize_unique_slugs!
      @mutex.synchronize do
        missing = @posts.values.select { |post| post.slug.nil? }
        unless missing.empty?
          raise MigrationIncomplete,
                "posts missing slugs: #{missing.map(&:id).sort.join(', ')}"
        end

        duplicates = @posts.values.group_by(&:slug).select { |_slug, posts| posts.length > 1 }
        unless duplicates.empty?
          raise DuplicateSlug,
                "duplicate slugs: #{duplicates.keys.sort.join(', ')}"
        end

        @posts.each_value do |post|
          unless @aliases[post.legacy_slug] == post.id
            raise MigrationIncomplete, "missing legacy alias for post #{post.id}"
          end

          owner = @posts.values.find do |candidate|
            candidate.id != post.id && candidate.slug == post.legacy_slug
          end
          if owner
            raise DuplicateSlug,
                  "legacy alias #{post.legacy_slug.inspect} conflicts with post #{owner.id}"
          end
        end

        @unique_slugs_enforced = true
      end
      true
    end

    def unique_slugs_enforced?
      @mutex.synchronize { @unique_slugs_enforced }
    end

    def post(id)
      @mutex.synchronize do
        found = @posts[id]
        found && copy_post(found)
      end
    end

    def find_by_slug(value)
      @mutex.synchronize do
        canonical = @posts.values.find { |post| post.slug == value }
        id = canonical&.id || @aliases[value]

        # Before a row is migrated, the old reader still consults legacy_slug.
        if id.nil?
          legacy = @posts.values.find do |post|
            post.slug.nil? && post.legacy_slug == value
          end
          id = legacy&.id
        end

        id && copy_post(@posts.fetch(id))
      end
    end

    def write_count(id)
      @mutex.synchronize { @write_counts[id] }
    end

    private

    def load_post(attributes)
      id = attributes.fetch(:id)
      raise ArgumentError, "duplicate post id #{id}" if @posts.key?(id)

      legacy_slug = attributes.fetch(:legacy_slug)
      if @posts.values.any? { |post| post.legacy_slug == legacy_slug }
        raise ArgumentError, "duplicate legacy slug #{legacy_slug.inspect}"
      end

      @posts[id] = Post.new(
        id: id,
        title: attributes.fetch(:title),
        legacy_slug: legacy_slug,
        slug: attributes[:slug]
      )
    end

    def load_existing_aliases
      @posts.each_value do |post|
        install_alias_locked(post) if post.slug
      end
    end

    def add_post_locked(id:, title:, legacy_slug:, slug:)
      raise ArgumentError, "id must be a positive integer" unless id.is_a?(Integer) && id.positive?
      raise ArgumentError, "title must be a string" unless title.is_a?(String)
      unless legacy_slug.is_a?(String) && !legacy_slug.empty?
        raise ArgumentError, "legacy_slug must be a non-empty string"
      end
      raise ArgumentError, "duplicate post id #{id}" if @posts.key?(id)
      if slug_unavailable_locked?(legacy_slug, except_id: id)
        raise DuplicateSlug, "legacy slug #{legacy_slug.inspect} is already in use"
      end
      if slug && slug_unavailable_locked?(slug, except_id: id)
        raise DuplicateSlug, "slug #{slug.inspect} is already in use"
      end

      @posts[id] = Post.new(
        id: id,
        title: title,
        legacy_slug: legacy_slug,
        slug: slug
      )
    end

    def install_alias_locked(post)
      owner = @aliases[post.legacy_slug]
      if owner && owner != post.id
        raise DuplicateSlug, "legacy alias #{post.legacy_slug.inspect} is already in use"
      end
      @aliases[post.legacy_slug] = post.id
    end

    def next_available_slug_locked(base, except_id:)
      suffix = 1
      loop do
        candidate = suffix == 1 ? base : "#{base}-#{suffix}"
        return candidate unless slug_unavailable_locked?(candidate, except_id: except_id)

        suffix += 1
      end
    end

    def slug_unavailable_locked?(value, except_id:)
      reserved_by = @slug_reservations[value]
      return true if reserved_by && reserved_by != except_id

      @posts.values.any? do |post|
        post.id != except_id &&
          (post.slug == value || post.legacy_slug == value)
      end
    end

    def next_id_locked
      (@posts.keys.max || 0) + 1
    end

    def copy_post(post)
      Post.new(
        id: post.id,
        title: post.title.dup,
        legacy_slug: post.legacy_slug.dup,
        slug: post.slug&.dup
      )
    end
  end

  class Rollout
    def initialize(store)
      @store = store
    end

    def expand!
      @store.expand!
    end

    def backfill_batch(cursor: nil, limit:, before_slug_commit: nil)
      posts, next_cursor, done = @store.scan_after(cursor: cursor, limit: limit)
      updated_ids = []

      posts.each do |post|
        _slug, changed = @store.claim_slug(
          post.id,
          before_commit: before_slug_commit
        )
        updated_ids << post.id if changed
      end

      BatchResult.new(
        cursor: next_cursor,
        scanned_ids: posts.map(&:id),
        updated_ids: updated_ids,
        done: done
      )
    end

    def finalize!
      @store.finalize_unique_slugs!
    end
  end
end

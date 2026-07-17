package dupfinder

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"syscall"
	"testing"
)

func writeFile(t *testing.T, root, rel string, content []byte) string {
	t.Helper()
	p := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, content, 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func hashOf(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func find(t *testing.T, root string, opts Options) *Report {
	t.Helper()
	rep, err := Find(root, opts)
	if err != nil {
		t.Fatalf("Find: %v", err)
	}
	if rep == nil {
		t.Fatal("Find returned a nil report without an error")
	}
	return rep
}

func TestGroupsSizesHashesAndWaste(t *testing.T) {
	root := t.TempDir()
	alpha := []byte("duplicate content alpha\n") // 24 bytes
	img := make([]byte, 4096)
	for i := range img {
		img[i] = byte(i % 251)
	}
	writeFile(t, root, "notes/a.txt", alpha)
	writeFile(t, root, "b.txt", alpha)
	writeFile(t, root, "deep/nested/c.txt", alpha)
	writeFile(t, root, "img1.bin", img)
	writeFile(t, root, "backup/img1-copy.bin", img)
	writeFile(t, root, "unique.txt", []byte("nothing else looks like this one\n"))
	writeFile(t, root, "same-size-1.dat", []byte("AAAA")) // same size,
	writeFile(t, root, "same-size-2.dat", []byte("BBBB")) // different bytes

	rep := find(t, root, Options{Workers: 2})
	want := []Group{
		{Size: 4096, Hash: hashOf(img), Paths: []string{"backup/img1-copy.bin", "img1.bin"}},
		{Size: 24, Hash: hashOf(alpha), Paths: []string{"b.txt", "deep/nested/c.txt", "notes/a.txt"}},
	}
	if !reflect.DeepEqual(rep.Groups, want) {
		t.Fatalf("Groups mismatch\n got: %+v\nwant: %+v", rep.Groups, want)
	}
	if wantWaste := int64(4096*1 + 24*2); rep.WastedBytes != wantWaste {
		t.Fatalf("WastedBytes = %d, want %d", rep.WastedBytes, wantWaste)
	}
}

func TestGroupOrderingSizeDescThenFirstPath(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "m1.dat", []byte(strings.Repeat("x", 100)))
	writeFile(t, root, "m2.dat", []byte(strings.Repeat("x", 100)))
	writeFile(t, root, "z2.dat", []byte(strings.Repeat("y", 100)))
	writeFile(t, root, "a1.dat", []byte(strings.Repeat("y", 100)))
	writeFile(t, root, "big2.dat", []byte(strings.Repeat("B", 500)))
	writeFile(t, root, "big1.dat", []byte(strings.Repeat("B", 500)))

	rep := find(t, root, Options{Workers: 3})
	var got [][]string
	for _, g := range rep.Groups {
		got = append(got, g.Paths)
	}
	want := [][]string{
		{"big1.dat", "big2.dat"},
		{"a1.dat", "z2.dat"},
		{"m1.dat", "m2.dat"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("group order/paths = %v, want %v (size desc, ties by first path)", got, want)
	}
}

func TestEmptyFilesAreNeverDuplicates(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "one.lock", nil)
	writeFile(t, root, "two.lock", nil)
	rep := find(t, root, Options{Workers: 1})
	if len(rep.Groups) != 0 {
		t.Fatalf("zero-byte files must never be grouped, got %+v", rep.Groups)
	}
	if rep.WastedBytes != 0 {
		t.Fatalf("WastedBytes = %d, want 0", rep.WastedBytes)
	}
}

func TestMinSizeFiltersSmallFiles(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "ten-a.txt", []byte("0123456789"))
	writeFile(t, root, "ten-b.txt", []byte("0123456789"))
	writeFile(t, root, "three-a.txt", []byte("abc"))
	writeFile(t, root, "three-b.txt", []byte("abc"))

	rep := find(t, root, Options{Workers: 2, MinSize: 10})
	if len(rep.Groups) != 1 || rep.Groups[0].Size != 10 {
		t.Fatalf("MinSize 10 must keep exactly the 10-byte group (a file of exactly MinSize stays in), got %+v", rep.Groups)
	}

	rep = find(t, root, Options{Workers: 2, MinSize: 11})
	if len(rep.Groups) != 0 {
		t.Fatalf("MinSize 11 must exclude everything, got %+v", rep.Groups)
	}
}

func TestSymlinksAreNeitherFollowedNorReported(t *testing.T) {
	root := t.TempDir()
	dup := []byte("symlink test payload, forty bytes long!\n")
	writeFile(t, root, "real/a.dat", dup)
	writeFile(t, root, "real/b.dat", dup)
	if err := os.Symlink(filepath.Join(root, "real", "a.dat"), filepath.Join(root, "link-to-a.dat")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := os.Symlink(filepath.Join(root, "real"), filepath.Join(root, "mirror")); err != nil {
		t.Fatal(err)
	}
	// A loop back up the tree: following symlinked dirs would walk forever.
	if err := os.Symlink(root, filepath.Join(root, "real", "loop")); err != nil {
		t.Fatal(err)
	}

	rep := find(t, root, Options{Workers: 2})
	want := []Group{{Size: int64(len(dup)), Hash: hashOf(dup), Paths: []string{"real/a.dat", "real/b.dat"}}}
	if !reflect.DeepEqual(rep.Groups, want) {
		t.Fatalf("symlinks leaked into the report\n got: %+v\nwant: %+v", rep.Groups, want)
	}
}

func TestHardlinkAliasesAreOneFile(t *testing.T) {
	root := t.TempDir()
	content := []byte(strings.Repeat("H", 64))
	writeFile(t, root, "a/base.bin", content)
	if err := os.MkdirAll(filepath.Join(root, "b"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(filepath.Join(root, "a", "base.bin"), filepath.Join(root, "b", "alias.bin")); err != nil {
		t.Skipf("hardlinks unavailable: %v", err)
	}

	rep := find(t, root, Options{Workers: 2})
	if len(rep.Groups) != 0 {
		t.Fatalf("two names for one inode are not duplicates, got %+v", rep.Groups)
	}

	// A genuinely distinct copy does make a group — with the hardlink pair
	// collapsed onto its lexicographically smallest path.
	writeFile(t, root, "c/copy.bin", content)
	rep = find(t, root, Options{Workers: 2})
	want := []Group{{Size: 64, Hash: hashOf(content), Paths: []string{"a/base.bin", "c/copy.bin"}}}
	if !reflect.DeepEqual(rep.Groups, want) {
		t.Fatalf("hardlink collapse wrong\n got: %+v\nwant: %+v", rep.Groups, want)
	}
	if rep.WastedBytes != 64 {
		t.Fatalf("WastedBytes = %d, want 64 (hardlinks share storage)", rep.WastedBytes)
	}
}

func TestUniqueSizeFilesAreNeverOpened(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("permission bits do not bind root")
	}
	root := t.TempDir()
	writeFile(t, root, "dup-1.txt", []byte("twenty bytes exactly"))
	writeFile(t, root, "dup-2.txt", []byte("twenty bytes exactly"))
	secret := writeFile(t, root, "secret.dat", []byte(strings.Repeat("s", 137)))
	if err := os.Chmod(secret, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(secret, 0o644) })

	rep := find(t, root, Options{Workers: 2}) // must not touch secret.dat: its size is unique
	if len(rep.Groups) != 1 || len(rep.Groups[0].Paths) != 2 {
		t.Fatalf("expected exactly the dup-1/dup-2 group, got %+v", rep.Groups)
	}
	for _, g := range rep.Groups {
		for _, p := range g.Paths {
			if strings.Contains(p, "secret") {
				t.Fatalf("unreadable unique-size file surfaced in a group: %+v", g)
			}
		}
	}
}

func TestUnreadableHashCandidateFailsWithItsPath(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("permission bits do not bind root")
	}
	root := t.TempDir()
	writeFile(t, root, "pair/x.dat", []byte(strings.Repeat("1", 33)))
	locked := writeFile(t, root, "pair/y.dat", []byte(strings.Repeat("2", 33)))
	if err := os.Chmod(locked, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(locked, 0o644) })

	_, err := Find(root, Options{Workers: 2})
	if err == nil {
		t.Fatal("a hash candidate that cannot be read must fail the scan")
	}
	if !strings.Contains(err.Error(), "y.dat") {
		t.Fatalf("error must name the unreadable file, got: %v", err)
	}
}

func TestNamedPipesAreIgnored(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("mkfifo test is linux-only")
	}
	root := t.TempDir()
	dup := []byte("fifo-adjacent duplicate data\n")
	writeFile(t, root, "d1.dat", dup)
	writeFile(t, root, "d2.dat", dup)
	if err := syscall.Mkfifo(filepath.Join(root, "stale.fifo"), 0o600); err != nil {
		t.Skipf("mkfifo unavailable: %v", err)
	}

	// Opening the FIFO would block forever; the scan must never do that.
	rep := find(t, root, Options{Workers: 2})
	want := []Group{{Size: int64(len(dup)), Hash: hashOf(dup), Paths: []string{"d1.dat", "d2.dat"}}}
	if !reflect.DeepEqual(rep.Groups, want) {
		t.Fatalf("FIFO changed the result\n got: %+v\nwant: %+v", rep.Groups, want)
	}
}

func TestArgumentValidation(t *testing.T) {
	root := t.TempDir()
	if _, err := Find(filepath.Join(root, "missing"), Options{Workers: 1}); err == nil {
		t.Fatal("a missing root must be an error")
	}
	file := writeFile(t, root, "plain.txt", []byte("x"))
	if _, err := Find(file, Options{Workers: 1}); err == nil {
		t.Fatal("a non-directory root must be an error")
	}
	if _, err := Find(root, Options{Workers: 0}); err == nil {
		t.Fatal("Workers: 0 must be an error")
	}
	if _, err := Find(root, Options{Workers: -2}); err == nil {
		t.Fatal("negative Workers must be an error")
	}
}

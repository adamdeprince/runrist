;;; emacs-extract.el --- write out what Emacs knows about a language  -*- lexical-binding: t -*-

;; This file is part of runrist.  GPL-3.0-or-later; see COPYING.

;; Usage:
;;   MODES="python-mode sh-mode ..." OUT=DIR emacs --batch -Q -l tools/emacs-extract.el </dev/null
;;
;; For each major mode named, this turns the mode on in a scratch buffer and
;; writes OUT/MODE.json: its syntax table, its font-lock keywords after
;; Emacs has compiled them (so `regexp-opt' and `rx' forms are already plain
;; regular expressions), its comment syntax, the file names and interpreters
;; that select it, and its hideshow block patterns.  runrist reads these files
;; and highlights with them; see runrist/modes/emacs.py.
;;
;; The files this writes are derived from GNU Emacs and carry its license.
;; Lisp code cannot be carried over this way: a matcher that is a function is
;; recorded by name, so that a Python port registered under that name can
;; stand in for it, and a face that is computed by a form is recorded as the
;; form's text.

(require 'font-lock)
(require 'json)
(require 'cl-lib)

(defconst rr-classes " .w_()'\"$\\/<>@!|")

(defun rr-syntax-entry (table char)
  "Return [CLASS FLAGS MATCH] for CHAR in TABLE, or nil if it has no entry."
  (let ((d (aref table char)))
    (when (consp d)
      (let* ((code (car d))
             (class (logand code 65535))
             (flags (ash code -16))
             (text ""))
        (dotimes (i 8)
          (when (/= 0 (logand flags (ash 1 i)))
            (setq text (concat text (string (aref "1234pbnc" i))))))
        (vector (string (aref rr-classes class)) text
                (if (cdr d) (string (cdr d)) ""))))))

(defun rr-syntax-table (table)
  "Return an alist of the ASCII entries of TABLE, keyed by character code."
  (let (out)
    (dotimes (c 128)
      (let ((e (rr-syntax-entry table c)))
        (when e (push (cons (number-to-string c) e) out))))
    (nreverse out)))

(defvar rr-faces nil "Faces the keywords of the mode being extracted refer to.")

(defun rr-note-face (face)
  (when (and face (symbolp face) (facep face))
    (cl-pushnew face rr-faces))
  (symbol-name face))

(defun rr-face (form)
  "Return the face FORM names, or an alist saying it has to be evaluated."
  (cond ((null form) nil)
        ((stringp form) form)
        ((and (consp form) (eq (car form) 'quote) (symbolp (cadr form)))
         (rr-note-face (cadr form)))
        ((and (consp form) (eq (car form) 'quote) (consp (cadr form))
              (cl-every #'symbolp (cadr form)))
         (vconcat (mapcar #'rr-note-face (cadr form))))
        ((and (symbolp form) (boundp form) (symbolp (symbol-value form))
              (symbol-value form))
         (rr-note-face (symbol-value form)))
        ((and (symbolp form) (facep form)) (rr-note-face form))
        (t (list (cons 'eval (prin1-to-string form))))))

(defun rr-face-look (face)
  "Return what FACE inherits from and the plain attributes it sets."
  (let ((inherit (face-attribute face :inherit nil nil))
        (weight (face-attribute face :weight nil nil))
        (slant (face-attribute face :slant nil nil))
        (underline (face-attribute face :underline nil nil)))
    (list (cons 'inherit (cond ((and (symbolp inherit) (facep inherit)) (symbol-name inherit))
                               ((and (consp inherit) (symbolp (car inherit)) (facep (car inherit)))
                                (symbol-name (car inherit)))))
          (cons 'bold (if (memq weight '(bold semi-bold extra-bold ultra-bold)) t :json-false))
          (cons 'italic (if (memq slant '(italic oblique)) t :json-false))
          (cons 'underline (if (and underline (not (eq underline 'unspecified))) t :json-false)))))

(defun rr-ancestors (mode)
  (let (out (p mode))
    (while (setq p (get p 'derived-mode-parent))
      (push (symbol-name p) out))
    (vconcat (nreverse out))))

(defun rr-matcher (matcher)
  (cond ((stringp matcher) (list (cons 're matcher)))
        ((symbolp matcher) (list (cons 'fn (symbol-name matcher))))
        (t (list (cons 'fn "") (cons 'code (let ((print-length 40) (print-level 6))
                                             (prin1-to-string matcher)))))))

(defun rr-highlight (h)
  (cond ((numberp (car h))
         (list (cons 'subexp (car h))
               (cons 'face (rr-face (nth 1 h)))
               (cons 'override (let ((o (nth 2 h))) (if o (symbol-name o) nil)))
               (cons 'lax (if (nth 3 h) t :json-false))))
        (t
         (list (cons 'anchor (rr-matcher (car h)))
               (cons 'pre (and (nth 1 h) (prin1-to-string (nth 1 h))))
               (cons 'post (and (nth 2 h) (prin1-to-string (nth 2 h))))
               (cons 'highlights (vconcat (mapcar #'rr-highlight (nthcdr 3 h))))))))

(defun rr-keywords ()
  "Return the buffer's compiled font-lock keywords."
  (let ((kw font-lock-keywords))
    (unless (eq (car kw) t)
      (setq kw (font-lock-compile-keywords kw)))
    (setq kw (cddr kw))
    (vconcat
     (mapcar (lambda (k)
               (list (cons 'matcher (rr-matcher (car k)))
                     (cons 'highlights
                           (vconcat (mapcar #'rr-highlight
                                            (cl-remove-if-not #'consp (cdr k)))))))
             kw))))

(defun rr-string-or-nil (value)
  (and (stringp value) value))

(defun rr-file-patterns (mode alist)
  (let (out)
    (dolist (entry alist)
      (when (and (stringp (car entry))
                 (or (eq (cdr entry) mode)
                     (and (consp (cdr entry)) (eq (cadr entry) mode))))
        (push (car entry) out)))
    (vconcat (nreverse out))))

(defun rr-extract (mode out)
  (with-temp-buffer
    (let ((buffer-file-name nil)
          (font-lock-maximum-decoration t))
      (setq rr-faces nil)
      (delay-mode-hooks (funcall mode))
      (font-lock-set-defaults)
      (let* ((hs (and (boundp 'hs-special-modes-alist)
                      (assq mode hs-special-modes-alist)))
             (keywords (rr-keywords))
             (data
              (list
               (cons 'mode (symbol-name mode))
               (cons 'title (if (stringp mode-name) mode-name ""))
               (cons 'ancestors (rr-ancestors mode))
               (cons 'emacs emacs-version)
               (cons 'source (let ((f (symbol-file mode 'defun)))
                               (and f (file-name-nondirectory
                                       (file-name-sans-extension f)))))
               (cons 'case_fold (if font-lock-keywords-case-fold-search t :json-false))
               (cons 'keywords_only (if font-lock-keywords-only t :json-false))
               (cons 'comment_start (rr-string-or-nil comment-start))
               (cons 'comment_end (rr-string-or-nil comment-end))
               (cons 'comment_start_skip (rr-string-or-nil comment-start-skip))
               (cons 'indent_tabs (if indent-tabs-mode t :json-false))
               (cons 'syntax (rr-syntax-table (syntax-table)))
               (cons 'font_lock_syntax
                     (and font-lock-syntax-table
                          (rr-syntax-table font-lock-syntax-table)))
               (cons 'keywords keywords)
               (cons 'faces (mapcar (lambda (face) (cons (symbol-name face) (rr-face-look face)))
                                    (cl-remove-if (lambda (face)
                                                    (string-prefix-p "font-lock-" (symbol-name face)))
                                                  rr-faces)))
               (cons 'auto_mode (rr-file-patterns mode auto-mode-alist))
               (cons 'interpreters (rr-file-patterns mode interpreter-mode-alist))
               (cons 'hs_start (rr-string-or-nil
                                (or (nth 1 hs) (bound-and-true-p hs-block-start-regexp))))
               (cons 'hs_end (rr-string-or-nil
                              (or (nth 2 hs) (bound-and-true-p hs-block-end-regexp))))
               (cons 'outline_regexp (rr-string-or-nil (bound-and-true-p outline-regexp)))
               (cons 'propertize (if syntax-propertize-function t :json-false)))))
        (let ((coding-system-for-write 'utf-8-unix)
              (json-encoding-pretty-print nil))
          (write-region (json-encode data) nil
                        (expand-file-name (concat (symbol-name mode) ".json") out)
                        nil 'silent))))))

(let ((out (or (getenv "OUT") default-directory)))
  (make-directory out t)
  (dolist (name (split-string (or (getenv "MODES") "")))
    (let ((mode (intern name)))
      (condition-case err
          (with-timeout (20 (princ (format "%s: timed out\n" name)))
            (rr-extract mode out)
            (princ (format "%s: ok\n" name)))
        (error (princ (format "%s: %S\n" name err)))))))

;;; emacs-extract.el ends here

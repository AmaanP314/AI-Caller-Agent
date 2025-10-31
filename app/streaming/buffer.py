from typing import Optional

class SentenceBuffer:
    """
    Buffers LLM tokens and segments them into complete sentences.
    """

    def __init__(self, min_words: int = 10):
        self.min_words = min_words
        self.buffer = ""
        self.word_count = 0
        self.is_final = False
        self.abbreviations = {
            'Dr.', 'Mr.', 'Mrs.', 'Ms.', 'Prof.', 'Sr.', 'Jr.',
            'etc.', 'i.e.', 'e.g.', 'vs.', 'Inc.', 'Ltd.', 'Co.'
        }

    def add_token(self, token: str) -> Optional[str]:
        """Add a token and return a complete sentence if ready."""
        self.buffer += token

        if token.strip():
            self.word_count += len(token.split())

        sentence_terminators = ['.', '?', '!']

        for terminator in sentence_terminators:
            if terminator in self.buffer:
                last_term_idx = self.buffer.rfind(terminator)
                after_terminator = self.buffer[last_term_idx + 1:].lstrip() # Use lstrip
                potential_sentence = self.buffer[:last_term_idx + 1].strip()

                if self._is_valid_sentence_boundary(potential_sentence, after_terminator):
                    sentence_word_count = len(potential_sentence.split())
                    
                    if sentence_word_count >= self.min_words or (self.is_final and sentence_word_count > 0):
                        sentence = potential_sentence
                        self.buffer = after_terminator
                        self.word_count = len(self.buffer.split())
                        return sentence
        return None

    def _is_valid_sentence_boundary(self, potential_sentence: str, after_text: str) -> bool:
        """Check for false positives like abbreviations."""
        words = potential_sentence.split()
        if words:
            last_word = words[-1]
            if last_word in self.abbreviations:
                return False

        if potential_sentence[-3:].replace('.', '').isdigit():
            return False

        # Valid if followed by space, newline, or end of text (empty)
        # or if the next character is uppercase
        if not after_text or after_text[0].isupper() or after_text[0].isspace():
            return True

        return False

    def mark_final(self) -> Optional[str]:
        """Mark stream as complete and flush remaining content."""
        self.is_final = True
        
        # Try one last time to split
        if self.buffer.strip():
            sentence = self.add_token("") # Re-run logic
            if sentence:
                return sentence

        if self.buffer.strip():
            sentence = self.buffer.strip()
            self.buffer = ""
            return sentence
        return None

    def has_content(self) -> bool:
        """Check if buffer has any content"""
        return bool(self.buffer.strip())

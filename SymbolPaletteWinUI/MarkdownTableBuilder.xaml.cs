using System;
using System.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace SymbolPaletteWinUI.Controls
{
    /// <summary>
    /// Builds a GitHub-flavored Markdown table from a chosen column and
    /// row count, raising <see cref="InsertRequested"/> with text ready
    /// to paste into a Jupyter markdown cell. The header captions and
    /// body cells are placeholders ("Header 1", "Cell") that the user
    /// overwrites in the notebook — the same placeholder approach used
    /// for the wrapper snippets.
    /// </summary>
    public sealed partial class MarkdownTableBuilder : UserControl, IKeyboardActivatedTab
    {
        /// <summary>
        /// Raised when Insert is clicked. The argument is the finished
        /// payload — the table grid with the surrounding blank lines a
        /// Markdown table needs — and requires no further processing.
        /// </summary>
        public event EventHandler<string>? InsertRequested;

        public MarkdownTableBuilder()
        {
            InitializeComponent();
            Loaded += (_, _) => RefreshPreview();
        }

        /// <summary>Part of IKeyboardActivatedTab — focus the first field.</summary>
        public void FocusInput() => ColumnsBox.Focus(FocusState.Programmatic);

        private void OnNumberChanged(
            NumberBox sender, NumberBoxValueChangedEventArgs args)
            => RefreshPreview();

        private void OnAlignChanged(object sender, SelectionChangedEventArgs e)
            => RefreshPreview();

        private void RefreshPreview()
        {
            // The Value="3" / IsSelected="True" attributes in the XAML can
            // raise these change events during InitializeComponent, before
            // every named element exists. Guard against the not-yet-built
            // preview target.
            if (PreviewText is null)
                return;

            PreviewText.Text = BuildTable();
        }

        private void Insert_Click(object sender, RoutedEventArgs e)
        {
            // A Markdown table only renders when a blank line precedes it,
            // so the payload leads with two newlines (one to end whatever
            // is on the current line, one blank separator) and ends with
            // one. Harmless extra whitespace if the cell was already empty.
            string payload = "\n\n" + BuildTable() + "\n";
            InsertRequested?.Invoke(this, payload);
        }

        private string BuildTable()
        {
            int columns = ReadCount(ColumnsBox, fallback: 3, min: 1, max: 10);
            int rows    = ReadCount(RowsBox,    fallback: 3, min: 1, max: 30);
            string separator = AlignmentSeparator();

            var sb = new StringBuilder();

            // Header row — placeholder captions.
            sb.Append('|');
            for (int c = 1; c <= columns; c++)
                sb.Append(" Header ").Append(c).Append(" |");
            sb.Append('\n');

            // Separator row — also carries the column alignment.
            sb.Append('|');
            for (int c = 0; c < columns; c++)
                sb.Append(' ').Append(separator).Append(" |");

            // Body rows — placeholder cells.
            for (int r = 0; r < rows; r++)
            {
                sb.Append('\n').Append('|');
                for (int c = 0; c < columns; c++)
                    sb.Append(" Cell |");
            }

            return sb.ToString();
        }

        /// <summary>
        /// The separator-row token that encodes column alignment in GFM:
        /// plain dashes (left), colons both sides (center), trailing
        /// colon (right). Applied to every column.
        /// </summary>
        private string AlignmentSeparator()
        {
            string choice =
                (AlignBox?.SelectedItem as ComboBoxItem)?.Content as string
                ?? "Left";

            return choice switch
            {
                "Center" => ":---:",
                "Right"  => "---:",
                _        => "---",
            };
        }

        private static int ReadCount(NumberBox box, int fallback, int min, int max)
        {
            // NumberBox.Value is NaN while the field is empty / mid-edit.
            double value = box?.Value ?? fallback;
            if (double.IsNaN(value))
                value = fallback;
            return Math.Clamp((int)value, min, max);
        }
    }
}

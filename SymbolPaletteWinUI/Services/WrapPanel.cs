using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Foundation;

namespace SymbolPaletteWinUI.Controls;

/// <summary>
/// A horizontal-orientation wrap panel that lets each child take its own
/// natural width.
///
/// Why this exists: WinUI 3's <c>ItemsWrapGrid</c> (the default panel
/// inside <c>GridView</c>) measures all items at the size of the first
/// item.  When the first item is a single glyph like "π" and a later item
/// is "update_currency_rates()", the long item gets clipped because every
/// "cell" in the grid is sized for "π".
///
/// This panel measures each child individually and lays them out left-to-
/// right, wrapping to a new row when the next child wouldn't fit.  Spacing
/// is configurable via <see cref="HorizontalSpacing"/> and
/// <see cref="VerticalSpacing"/>.
///
/// Limits / non-features:
///   - Horizontal-only.  A vertical layout would need the symmetric logic;
///     this panel only does what's needed for the palette.
///   - No alignment within rows (left-justified always).  Adding centre or
///     stretch alignment would require a second pass, which isn't needed
///     here.
///   - Children should be reasonably small relative to the available width.
///     If a single child is wider than the available width it'll overflow
///     the row; we don't shrink content.
/// </summary>
public sealed class WrapPanel : Panel
{
    public double HorizontalSpacing
    {
        get => (double)GetValue(HorizontalSpacingProperty);
        set => SetValue(HorizontalSpacingProperty, value);
    }

    public static readonly DependencyProperty HorizontalSpacingProperty =
        DependencyProperty.Register(
            nameof(HorizontalSpacing),
            typeof(double),
            typeof(WrapPanel),
            new PropertyMetadata(6.0, OnLayoutInvalidated));

    public double VerticalSpacing
    {
        get => (double)GetValue(VerticalSpacingProperty);
        set => SetValue(VerticalSpacingProperty, value);
    }

    public static readonly DependencyProperty VerticalSpacingProperty =
        DependencyProperty.Register(
            nameof(VerticalSpacing),
            typeof(double),
            typeof(WrapPanel),
            new PropertyMetadata(6.0, OnLayoutInvalidated));

    private static void OnLayoutInvalidated(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is WrapPanel panel)
        {
            panel.InvalidateMeasure();
            panel.InvalidateArrange();
        }
    }

    protected override Size MeasureOverride(Size availableSize)
    {
        // When height is unconstrained (the common case inside a vertical
        // StackPanel), we still need a finite width for the wrap math.
        // PositiveInfinity for width means "lay everything in one row";
        // we surface that as the row's total width.
        double availableWidth = double.IsInfinity(availableSize.Width)
            ? double.PositiveInfinity
            : availableSize.Width;

        double rowWidth = 0;
        double rowHeight = 0;
        double maxRowWidth = 0;
        double totalHeight = 0;

        foreach (var child in Children)
        {
            child.Measure(new Size(availableWidth, double.PositiveInfinity));
            var childSize = child.DesiredSize;

            // Width this child would push the row to, accounting for the
            // gap before it (no gap if it's first in the row).
            double widthIfPlaced = rowWidth == 0
                ? childSize.Width
                : rowWidth + HorizontalSpacing + childSize.Width;

            if (rowWidth > 0 && widthIfPlaced > availableWidth)
            {
                // Wrap: finalise current row, start a new one with this
                // child as the first item.
                totalHeight += rowHeight + VerticalSpacing;
                maxRowWidth = Math.Max(maxRowWidth, rowWidth);
                rowWidth = childSize.Width;
                rowHeight = childSize.Height;
            }
            else
            {
                rowWidth = widthIfPlaced;
                rowHeight = Math.Max(rowHeight, childSize.Height);
            }
        }

        // Account for the last (possibly only) row.
        totalHeight += rowHeight;
        maxRowWidth = Math.Max(maxRowWidth, rowWidth);

        return new Size(maxRowWidth, totalHeight);
    }

    protected override Size ArrangeOverride(Size finalSize)
    {
        double x = 0;
        double y = 0;
        double rowHeight = 0;

        foreach (var child in Children)
        {
            var childSize = child.DesiredSize;

            // Same wrap decision as in Measure, using the FINAL allocated
            // width.  Recomputing here means we don't rely on cached state
            // between Measure and Arrange.
            double widthIfPlaced = x == 0
                ? childSize.Width
                : x + HorizontalSpacing + childSize.Width;

            if (x > 0 && widthIfPlaced > finalSize.Width)
            {
                x = 0;
                y += rowHeight + VerticalSpacing;
                rowHeight = 0;
            }

            // Add inter-item spacing AFTER the wrap decision, so the first
            // item on each row sits flush with the panel's left edge.
            if (x > 0)
                x += HorizontalSpacing;

            child.Arrange(new Rect(x, y, childSize.Width, childSize.Height));
            x += childSize.Width;
            rowHeight = Math.Max(rowHeight, childSize.Height);
        }

        return finalSize;
    }
}
